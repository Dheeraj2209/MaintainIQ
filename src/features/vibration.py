"""Time- and frequency-domain feature extraction for vibration signal windows.

Ported from research/ims_bearing_baseline/src/features.py, which validated
this feature set against the NASA IMS Bearing Dataset (real run-to-failure
vibration data). Kept dataset-agnostic here: callers supply a raw 1D signal
window and get back a flat feature dict, independent of channel/bearing
naming conventions specific to IMS.
"""
import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.stats import kurtosis, skew


def time_domain_features(signal: np.ndarray) -> dict:
    x = signal.astype(np.float64)
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


def freq_domain_features(signal: np.ndarray, sampling_rate_hz: float) -> dict:
    x = signal.astype(np.float64)
    n = len(x)
    freqs = rfftfreq(n, d=1 / sampling_rate_hz)
    mag = np.abs(rfft(x)) / n
    power = mag ** 2
    total_power = power.sum() if power.sum() > 0 else 1e-12

    dom_idx = np.argmax(mag[1:]) + 1  # skip DC component
    dominant_freq = freqs[dom_idx]
    spectral_centroid = np.sum(freqs * power) / total_power

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


def extract_window_features(signal: np.ndarray, sampling_rate_hz: float) -> dict:
    """Extract the full feature set for one vibration signal window."""
    return {**time_domain_features(signal), **freq_domain_features(signal, sampling_rate_hz)}


VIBRATION_FEATURE_COLUMNS = [
    "mean", "std", "rms", "peak", "peak_to_peak", "kurtosis", "skewness",
    "crest_factor", "shape_factor", "dominant_freq", "spectral_centroid",
    "spectral_energy", "low_band_energy_ratio", "mid_band_energy_ratio",
    "high_band_energy_ratio",
]
