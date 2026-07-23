"""
Feature extraction for IMS bearing vibration snapshots.
Each raw file = 1 second @ 20480 Hz, columns = accelerometer channels.
Test 1 channel map: Bearing1->col0,1 | Bearing2->col2,3 | Bearing3->col4,5 | Bearing4->col6,7
"""
import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew
from scipy.fft import rfft, rfftfreq

FS = 20480  # sampling rate (Hz)

# Channel maps per test set. Test 1 has 2 accelerometer channels per bearing
# (horizontal + vertical); Tests 2 and 3 have only 1 channel per bearing.
TEST1_CHANNELS = {"B1": (0, 1), "B2": (2, 3), "B3": (4, 5), "B4": (6, 7)}
TEST2_CHANNELS = {"B1": (0,), "B2": (1,), "B3": (2,), "B4": (3,)}
TEST3_CHANNELS = {"B1": (0,), "B2": (1,), "B3": (2,), "B4": (3,)}

# Kept for backwards compatibility with earlier Test-1-only code.
BEARING_CHANNELS = TEST1_CHANNELS


def time_domain_features(x: np.ndarray) -> dict:
    x = x.astype(np.float64)
    rms = np.sqrt(np.mean(x ** 2))
    peak = np.max(np.abs(x))
    return {
        "mean": np.mean(x),
        "std": np.std(x),
        "rms": rms,
        "peak": peak,
        "peak_to_peak": x.max() - x.min(),
        "kurtosis": kurtosis(x, fisher=True),
        "skewness": skew(x),
        "crest_factor": peak / rms if rms > 0 else 0.0,
        "shape_factor": rms / np.mean(np.abs(x)) if np.mean(np.abs(x)) > 0 else 0.0,
    }


def freq_domain_features(x: np.ndarray, fs: int = FS) -> dict:
    x = x.astype(np.float64)
    n = len(x)
    freqs = rfftfreq(n, d=1 / fs)
    mag = np.abs(rfft(x)) / n
    power = mag ** 2
    total_power = power.sum() if power.sum() > 0 else 1e-12

    dom_idx = np.argmax(mag[1:]) + 1  # skip DC component
    dominant_freq = freqs[dom_idx]
    spectral_centroid = np.sum(freqs * power) / total_power

    # crude energy split across low/mid/high thirds of the spectrum
    third = len(freqs) // 3
    low_energy = power[:third].sum() / total_power
    mid_energy = power[third:2 * third].sum() / total_power
    high_energy = power[2 * third:].sum() / total_power

    return {
        "dominant_freq": dominant_freq,
        "spectral_centroid": spectral_centroid,
        "spectral_energy": power.sum(),
        "low_band_energy_ratio": low_energy,
        "mid_band_energy_ratio": mid_energy,
        "high_band_energy_ratio": high_energy,
    }


def extract_file_features(filepath: str, channel_map: dict = None) -> dict:
    """Read one snapshot file and return a flat dict of features for all bearings.

    channel_map: dict of bearing_name -> tuple of column indices (1 or 2 entries).
    First index = primary channel, named 'h'. Second index (if present) = 'v'.
    Defaults to the Test 1 map (2 channels/bearing) for backwards compatibility.
    """
    if channel_map is None:
        channel_map = TEST1_CHANNELS
    arr = pd.read_csv(filepath, sep="\t", header=None, dtype=np.float32).values
    row = {}
    for bearing, cols in channel_map.items():
        ch_names = ["h", "v"][: len(cols)]
        for ch_name, col in zip(ch_names, cols):
            sig = arr[:, col]
            tdf = time_domain_features(sig)
            fdf = freq_domain_features(sig)
            for k, v in {**tdf, **fdf}.items():
                row[f"{bearing}_{ch_name}_{k}"] = v
    return row
