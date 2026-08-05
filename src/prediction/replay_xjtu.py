"""Replay an XJTU-SY bearing trajectory through real-time inference."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import numpy as np

from src.ingestion.xjtu_sy import SAMPLE_RATE_HZ, read_snapshot
from src.prediction.rul_realtime import RealTimeRULPredictor


def _snapshot_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    if match is None:
        raise ValueError(f"snapshot filename must end in a number: {path.name}")
    return int(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay raw XJTU-SY snapshots as live sensor input")
    parser.add_argument("--bearing-dir", type=Path, required=True)
    parser.add_argument("--speed-rpm", type=float, required=True)
    parser.add_argument("--load-kn", type=float, required=True)
    parser.add_argument("--sample-rate-hz", type=float, default=SAMPLE_RATE_HZ)
    parser.add_argument("--every", type=int, default=20, help="print every Nth snapshot")
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    snapshots = sorted(args.bearing_dir.glob("*.csv"), key=_snapshot_number)
    if not snapshots:
        parser.error(f"no CSV snapshots found in {args.bearing_dir}")
    predictor = RealTimeRULPredictor()
    records = []
    print(f"Replaying {len(snapshots)} snapshots from {args.bearing_dir.name}")
    for cycle, path in enumerate(snapshots):
        horizontal, vertical = read_snapshot(path)
        result = predictor.predict(
            machine_id=args.bearing_dir.name,
            horizontal=np.asarray(horizontal),
            vertical=np.asarray(vertical),
            sample_rate_hz=args.sample_rate_hz,
            speed_rpm=args.speed_rpm,
            load_kn=args.load_kn,
        )
        actual_rul = len(snapshots) - cycle - 1
        record = {
            "cycle": cycle,
            "actual_rul_minutes": actual_rul,
            "predicted_rul_minutes": result["predicted_rul_minutes"],
            "estimate_kind": result["rul_estimate_kind"],
            "failure_probability": result["failure_within_horizon_probability"],
            "health_state": result["health_state"],
            "out_of_distribution": result["out_of_distribution"],
        }
        records.append(record)
        if cycle % max(args.every, 1) == 0 or cycle == len(snapshots) - 1:
            symbol = ">" if result["rul_estimate_kind"] == "lower_bound" else "~"
            print(
                f"cycle={cycle:4d} actual={actual_rul:4d}m "
                f"predicted{symbol}{result['predicted_rul_minutes']:6.1f}m "
                f"P(fail<=2h)={result['failure_within_horizon_probability']:.3f} "
                f"state={result['health_state']}"
            )

    if args.output_csv is not None:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        print(f"Saved {args.output_csv}")


if __name__ == "__main__":
    main()
