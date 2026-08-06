"""M1-M3 end-to-end run: ingestion -> temperature synthesis -> rule-based
labeling -> candidate model benchmark/export -> ML/rule-based routing ->
probable root cause -> alert generation -> anomaly detection -> RUL (bonus)
-> SQLite persistence.

Not a production entry point (that's M4's FastAPI service) — this exists to
prove the pipeline holds together end-to-end and to print a KPI leaderboard
while the modules are still being built out.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.alerts.generation import generate_alerts  # noqa: E402
from src.legacy.temperature import synthesize_temperature  # noqa: E402
from src.legacy.ims_bearing import load_all_tests  # noqa: E402
from src.legacy import ml_model, router  # noqa: E402
from src.legacy.rule_based import add_health_score_and_stage  # noqa: E402
from src.root_cause.rule_based import add_probable_cause  # noqa: E402
from src.storage import db  # noqa: E402
from src.legacy.anomaly_detector import train_anomaly_detector  # noqa: E402
from src.legacy.export import export_winner  # noqa: E402
from src.legacy.rul_regressor import add_rul, train_leave_one_trajectory_out  # noqa: E402
from src.legacy.stage_classifiers import feature_columns, run_benchmark, select_winner, time_based_split  # noqa: E402


def add_synthetic_temperature(long_df):
    long_df = long_df.copy()
    long_df["temperature_c"] = float("nan")
    long_df["temperature_is_synthetic"] = True
    for machine_id, group in long_df.groupby("machine_id"):
        ordered = group.sort_values("timestamp")
        n_base = max(1, int(len(ordered) * 0.1))
        baseline_rms = ordered.iloc[:n_base]["vibration_h_rms"].mean()
        temps = synthesize_temperature(ordered["vibration_h_rms"], baseline_rms)
        long_df.loc[ordered.index, "temperature_c"] = temps.values
    return long_df


def route_predictions(long_df: pd.DataFrame, deployed) -> pd.DataFrame:
    """Add prediction_health_state / prediction_confidence / prediction_source
    by running the deployed ML model (if any) against every row and letting
    src/prediction/router.py decide, per row, whether to trust it or fall
    back to the already-computed rule-based health_state.
    """
    long_df = long_df.copy()
    threshold = router.get_confidence_threshold()

    if deployed is not None:
        X = long_df[deployed.feature_columns]
        ml_states = deployed.model.predict(X)
        if hasattr(deployed.model, "predict_proba"):
            ml_confidences = deployed.model.predict_proba(X).max(axis=1)
        else:
            ml_confidences = np.full(len(X), np.nan)
    else:
        ml_states = np.full(len(long_df), None)
        ml_confidences = np.full(len(long_df), np.nan)

    routed_states, routed_confidences, routed_sources = [], [], []
    for rule_state, ml_state, ml_conf in zip(long_df["health_state"], ml_states, ml_confidences):
        ml_pred = (ml_state, None if np.isnan(ml_conf) else float(ml_conf)) if ml_state is not None else None
        result = router.route_prediction(rule_state, ml_pred, threshold)
        routed_states.append(result["health_state"])
        routed_confidences.append(result["confidence"])
        routed_sources.append(result["source"])

    long_df["rule_based_health_state"] = long_df["health_state"]
    long_df["health_state"] = routed_states
    long_df["prediction_confidence"] = routed_confidences
    long_df["prediction_source"] = routed_sources
    return long_df, threshold


def persist_to_sqlite(long_df: pd.DataFrame, deployed) -> Path:
    conn = db.get_connection()
    db.init_schema(conn)
    db.insert_machines(conn, long_df)
    db.insert_readings(conn, long_df)

    reading_id_map = db.get_reading_id_map(conn)
    now = datetime.now(timezone.utc).isoformat()

    predictions = []
    for row in long_df.itertuples():
        reading_id = reading_id_map.get((row.machine_id, row.timestamp.isoformat()))
        if reading_id is None:
            continue
        predictions.append({
            "reading_id": reading_id,
            "machine_id": row.machine_id,
            "timestamp": row.timestamp.isoformat(),
            "health_state": row.health_state,
            "confidence": row.prediction_confidence,
            "source": row.prediction_source,
            "model_name": deployed.winner_name if deployed is not None and row.prediction_source == "ml" else "rule_based",
            "probable_cause": None if pd.isna(row.probable_cause) else row.probable_cause,
            "created_at": now,
        })
    db.insert_predictions(conn, predictions)

    alerts_df = long_df.copy()
    alerts_df["source"] = alerts_df["prediction_source"]
    alerts = generate_alerts(alerts_df)
    db.insert_alerts(conn, alerts)

    conn.close()
    return db.DEFAULT_DB_PATH, len(predictions), len(alerts)


def main():
    print("Loading IMS bearing dataset as multi-machine feature table...")
    long_df = load_all_tests()
    print(f"  {long_df['machine_id'].nunique()} machines, {len(long_df)} readings")

    long_df = add_health_score_and_stage(long_df)
    print("Rule-based health state distribution:")
    print(long_df["health_state"].value_counts().to_string())

    long_df = add_synthetic_temperature(long_df)
    long_df = add_rul(long_df)

    print("\nBenchmarking candidate stage classifiers...")
    results = run_benchmark(long_df)
    for name, r in results.items():
        conf = f"{r['mean_confidence']:.3f}" if r["mean_confidence"] is not None else "n/a"
        print(f"  {name:20s} accuracy={r['accuracy']:.3f}  false_alarms={r['false_alarm_count']:3d}  "
              f"missed_faults={r['missed_fault_count']:3d}  mean_confidence={conf}")
    winner = select_winner(results)
    print(f"  -> winner: {winner}")

    _, test_df = time_based_split(long_df)
    report = export_winner(results, winner, feature_columns(long_df), test_df)
    threshold_info = report["confidence_threshold_analysis"]
    print(f"  exported models/stage_classifier.joblib + models/evaluation_report.json")
    if threshold_info.get("available"):
        print(f"  confidence threshold analysis: median correct={threshold_info['median_confidence_correct']:.3f}  "
              f"median incorrect={threshold_info['median_confidence_incorrect']:.3f}  "
              f"-> suggested threshold={threshold_info['suggested_threshold']}")

    print("\nRouting predictions (ML-first, rule-based fallback)...")
    deployed = ml_model.load_deployed_model()
    long_df, threshold = route_predictions(long_df, deployed)
    print(f"  threshold={threshold}")
    print(long_df["prediction_source"].value_counts().to_string())

    long_df = add_probable_cause(long_df)
    print("\nProbable root cause distribution (abnormal readings only):")
    print(long_df["probable_cause"].value_counts(dropna=True).to_string())

    print("\nTraining anomaly detector (IsolationForest, healthy-baseline-only)...")
    _, scored = train_anomaly_detector(long_df)
    print(f"  flagged {int(scored['is_anomaly'].sum())} / {len(scored)} readings as anomalous")

    print("\nTraining RUL regressor (leave-one-trajectory-out)...")
    rul_results = train_leave_one_trajectory_out(long_df)
    for machine_id, r in rul_results.items():
        print(f"  {machine_id:12s} MAE={r['mae']:8.2f}h  RMSE={r['rmse']:8.2f}h  "
              f"(trained on {len(r['trained_on'])} other trajectories)")

    print("\nPersisting readings/predictions/alerts to SQLite...")
    db_path, n_predictions, n_alerts = persist_to_sqlite(long_df, deployed)
    print(f"  {db_path}: {len(long_df)} readings, {n_predictions} predictions, {n_alerts} alerts")


if __name__ == "__main__":
    main()
