"""Minimal cleaning for pre-windowed feature tables.

The IMS ingestion path (src/ingestion/ims_bearing.py) already produces
one row per fixed-length signal window, so there is no raw-signal windowing
to do here yet. This module exists so M6 (live sensor telemetry) has a
stable place to plug in real windowing/cleaning without changing the
downstream feature/prediction interfaces.
"""
import pandas as pd


def drop_invalid_readings(df: pd.DataFrame, feature_columns: list) -> pd.DataFrame:
    """Drop rows with missing or non-finite values in any of the given feature columns."""
    valid = df[feature_columns].apply(pd.to_numeric, errors="coerce").notna().all(axis=1)
    return df.loc[valid].reset_index(drop=True)


def window_signal(signal, window_size: int, hop_size: int = None):
    """Split a raw 1D signal into fixed-length windows.

    Placeholder for M6 (live hardware/raw-signal ingestion). The IMS dataset
    is pre-windowed at the source (1-second snapshots), so this isn't
    exercised yet.
    """
    hop = hop_size or window_size
    return [signal[i:i + window_size] for i in range(0, len(signal) - window_size + 1, hop)]
