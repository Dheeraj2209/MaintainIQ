"""
Full predictive maintenance pipeline on top of an extracted feature table.
Generalized to work across any IMS test set (1, 2, or 3), since Test 1 has
2 channels/bearing while Tests 2 and 3 have only 1.

  1. Build long-format (timestamp, bearing) table
  2. Label degradation stage (Normal / Degrading / Critical) via baseline z-score health index
  3. Label RUL (hours to end of trajectory) for known failing bearings
  4. Train + evaluate:
       - RandomForest classifier for stage
       - XGBoost regressor for RUL (leave-one-trajectory-out across tests)
       - IsolationForest for unsupervised anomaly detection
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.metrics import classification_report, confusion_matrix, mean_absolute_error, mean_squared_error
from xgboost import XGBRegressor

FEATURE_COLS = ['mean', 'std', 'rms', 'peak', 'peak_to_peak', 'kurtosis', 'skewness', 'crest_factor',
                'shape_factor', 'dominant_freq', 'spectral_centroid', 'spectral_energy',
                'low_band_energy_ratio', 'mid_band_energy_ratio', 'high_band_energy_ratio']

BEARINGS = ['B1', 'B2', 'B3', 'B4']

# Documented failure modes per test (IMS / Univ. of Cincinnati publications):
#   Test 1: B3 inner race defect, B4 roller element defect (data truncated, no true failure point)
#   Test 2: B1 outer race defect (full trajectory to actual failure)
#   Test 3: B3 outer race defect (full trajectory to actual failure)
FAILING_BEARINGS = ['B3', 'B4']  # kept for backwards compatibility (Test 1 default)


def build_long_table(df: pd.DataFrame, bearings=BEARINGS, source: str = None) -> pd.DataFrame:
    records = []
    for b in bearings:
        sub = pd.DataFrame({'timestamp': df['timestamp'], 'bearing': b})
        for feat in FEATURE_COLS:
            hcol = f'{b}_h_{feat}'
            vcol = f'{b}_v_{feat}'
            if hcol in df.columns:
                sub[f'h_{feat}'] = df[hcol]
            if vcol in df.columns:
                sub[f'v_{feat}'] = df[vcol]
        if source is not None:
            sub['source'] = source
        records.append(sub)
    long_df = pd.concat(records, ignore_index=True).sort_values(['bearing', 'timestamp']).reset_index(drop=True)
    return long_df


def add_health_score_and_stage(long_df: pd.DataFrame) -> pd.DataFrame:
    group_cols = ['source', 'bearing'] if 'source' in long_df.columns else ['bearing']

    def zscore_health(g):
        g = g.sort_values('timestamp')  # defensive: baseline must be the true first 10% by time,
        # regardless of what row order the caller passed in (build_long_table happens to
        # already sort this way, but this function shouldn't silently depend on that).
        n_base = max(20, int(len(g) * 0.1))
        base = g.iloc[:n_base]
        rms_mu, rms_sd = base['h_rms'].mean(), base['h_rms'].std()
        kur_mu, kur_sd = base['h_kurtosis'].mean(), base['h_kurtosis'].std()
        z_rms = (g['h_rms'] - rms_mu) / (rms_sd + 1e-9)
        z_kur = (g['h_kurtosis'] - kur_mu) / (kur_sd + 1e-9)
        return (z_rms.abs() + z_kur.abs()) / 2

    long_df = long_df.copy()
    long_df['health_score'] = long_df.groupby(group_cols, group_keys=False).apply(zscore_health)

    def stage(s):
        if s < 3:
            return 'Normal'
        elif s < 15:
            return 'Degrading'
        else:
            return 'Critical'

    long_df['stage'] = long_df['health_score'].apply(stage)
    return long_df


def add_rul(long_df: pd.DataFrame, failing_bearings, source: str = None) -> pd.DataFrame:
    """
    Adds an 'rul_hours' column (hours remaining until the end of the
    trajectory) for the given failing bearings. If `source` is given, only
    rows matching that source are touched (so this can be called repeatedly
    when combining multiple tests into one long_df).
    """
    long_df = long_df.copy()
    if 'rul_hours' not in long_df.columns:
        long_df['rul_hours'] = np.nan

    scope = long_df['source'] == source if source is not None else pd.Series(True, index=long_df.index)
    for b in failing_bearings:
        mask = scope & (long_df['bearing'] == b)
        end_time = long_df.loc[mask, 'timestamp'].max()
        long_df.loc[mask, 'rul_hours'] = (end_time - long_df.loc[mask, 'timestamp']).dt.total_seconds() / 3600.0
    return long_df


# kept for backwards compatibility with the Test-1-only notebook
def add_rul_proxy(long_df: pd.DataFrame) -> pd.DataFrame:
    return add_rul(long_df, FAILING_BEARINGS)


def feature_matrix(long_df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ([f'h_{f}' for f in FEATURE_COLS] + [f'v_{f}' for f in FEATURE_COLS])
            if c in long_df.columns]
    return long_df[cols]


def common_feature_matrix(long_df: pd.DataFrame) -> pd.DataFrame:
    """Only the primary ('h_') channel features -- the common ground across
    Test 1 (2 channels/bearing) and Tests 2/3 (1 channel/bearing), needed for
    any model trained across multiple test sets at once."""
    cols = [f'h_{f}' for f in FEATURE_COLS]
    return long_df[cols]


# ---------------------------------------------------------------------------
# Stage classification (time-based split, last 20% of each bearing = test)
# ---------------------------------------------------------------------------
def train_stage_classifier(long_df: pd.DataFrame, bearings=BEARINGS, use_common_features=False):
    train_idx, test_idx = [], []
    for b in bearings:
        sub = long_df[long_df.bearing == b].sort_values('timestamp')
        n_test = int(len(sub) * 0.2)
        train_idx.extend(sub.index[:-n_test])
        test_idx.extend(sub.index[-n_test:])

    train_df = long_df.loc[train_idx]
    test_df = long_df.loc[test_idx]

    fm = common_feature_matrix if use_common_features else feature_matrix
    X_train, y_train = fm(train_df), train_df['stage']
    X_test, y_test = fm(test_df), test_df['stage']

    clf = RandomForestClassifier(n_estimators=300, max_depth=12, class_weight='balanced', random_state=42)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    labels = [l for l in ['Normal', 'Degrading', 'Critical'] if l in set(y_test) | set(y_pred)]
    report = classification_report(y_test, y_pred, digits=3, labels=labels)
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    return clf, report, cm, labels, (X_train, y_train, X_test, y_test, y_pred, test_df)


# ---------------------------------------------------------------------------
# RUL regression -- leave-one-trajectory-out across (source, bearing) pairs
# ---------------------------------------------------------------------------
def train_rul_models(long_df: pd.DataFrame, trajectories=None):
    """
    trajectories: list of (source, bearing) tuples identifying each known
    failure trajectory. If long_df has no 'source' column, pass bearing names
    only (single-test case) via FAILING_BEARINGS-style list instead.
    Uses only the common ('h_') feature set so trajectories from tests with
    different channel counts can be mixed.
    """
    has_source = 'source' in long_df.columns
    rul_df = long_df.dropna(subset=['rul_hours']).copy()

    if trajectories is None:
        if has_source:
            trajectories = sorted(rul_df.loc[rul_df['rul_hours'].notna(), ['source', 'bearing']]
                                   .drop_duplicates().itertuples(index=False, name=None))
        else:
            trajectories = [(None, b) for b in rul_df['bearing'].unique()]

    def traj_mask(df, src, b):
        m = df['bearing'] == b
        if has_source and src is not None:
            m &= df['source'] == src
        return m

    results = {}
    for src, b in trajectories:
        test_mask = traj_mask(rul_df, src, b)
        train_mask = ~test_mask & rul_df.index.isin(rul_df.index)  # everything else with a valid RUL
        # restrict train to rows that belong to one of the OTHER trajectories
        other_trajs = [t for t in trajectories if t != (src, b)]
        train_mask = pd.Series(False, index=rul_df.index)
        for osrc, ob in other_trajs:
            train_mask |= traj_mask(rul_df, osrc, ob)

        train_df = rul_df[train_mask]
        test_df = rul_df[test_mask]
        if len(train_df) == 0 or len(test_df) == 0:
            continue

        X_train, y_train = common_feature_matrix(train_df), train_df['rul_hours']
        X_test, y_test = common_feature_matrix(test_df), test_df['rul_hours']

        model = XGBRegressor(n_estimators=400, max_depth=5, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, random_state=42)
        model.fit(X_train, y_train)
        y_pred = np.clip(model.predict(X_test), 0, None)

        mae = mean_absolute_error(y_test, y_pred)
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))

        key = f"{src}:{b}" if src is not None else b
        results[key] = {
            'model': model,
            'trained_on': [f"{s}:{bb}" if s is not None else bb for s, bb in other_trajs],
            'mae': mae,
            'rmse': rmse,
            'y_test': y_test,
            'y_pred': y_pred,
            'timestamps': test_df['timestamp'],
        }
    return results


def train_rul_models_naive_split(long_df: pd.DataFrame, bearings, test_frac: float = 0.3, source: str = None):
    """
    Naive baseline for comparison: chronological split WITHIN the same
    bearing's own trajectory. Included only to make visible how much easier
    (and less representative of real deployment) this is versus a proper
    leave-one-trajectory-out split.
    """
    rul_df = long_df.dropna(subset=['rul_hours'])
    if source is not None and 'source' in rul_df.columns:
        rul_df = rul_df[rul_df['source'] == source]

    results = {}
    for b in bearings:
        sub = rul_df[rul_df.bearing == b].sort_values('timestamp')
        if len(sub) < 10:
            continue
        n_test = int(len(sub) * test_frac)
        train_df = sub.iloc[:-n_test]
        test_df = sub.iloc[-n_test:]

        X_train, y_train = common_feature_matrix(train_df), train_df['rul_hours']
        X_test, y_test = common_feature_matrix(test_df), test_df['rul_hours']

        model = XGBRegressor(n_estimators=400, max_depth=5, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, random_state=42)
        model.fit(X_train, y_train)
        y_pred = np.clip(model.predict(X_test), 0, None)

        mae = mean_absolute_error(y_test, y_pred)
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        results[b] = {'mae': mae, 'rmse': rmse, 'y_test': y_test, 'y_pred': y_pred,
                       'timestamps': test_df['timestamp']}
    return results


# ---------------------------------------------------------------------------
# Anomaly detection (IsolationForest trained on healthy baseline only)
# ---------------------------------------------------------------------------
def train_anomaly_detector(long_df: pd.DataFrame, bearings=BEARINGS, use_common_features=False):
    group_cols = ['source', 'bearing'] if 'source' in long_df.columns else ['bearing']
    baseline_frames = []
    for keys, sub in long_df.groupby(group_cols):
        sub = sub.sort_values('timestamp')
        n_base = int(len(sub) * 0.1)
        baseline_frames.append(sub.iloc[:n_base])
    baseline_df = pd.concat(baseline_frames)

    fm = common_feature_matrix if use_common_features else feature_matrix
    X_base = fm(baseline_df)
    iso = IsolationForest(n_estimators=300, contamination=0.05, random_state=42)
    iso.fit(X_base)

    long_df = long_df.copy()
    X_all = fm(long_df)
    long_df['anomaly_score'] = -iso.score_samples(X_all)
    long_df['is_anomaly'] = iso.predict(X_all) == -1
    return iso, long_df
