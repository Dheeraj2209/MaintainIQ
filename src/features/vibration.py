"""Time- and frequency-domain feature extraction for vibration signal windows.

Ported from research/ims_bearing_baseline/src/features.py, which validated
this feature set against the NASA IMS Bearing Dataset (real run-to-failure
vibration data). Kept dataset-agnostic here: callers supply a raw 1D signal
window and get back a flat feature dict, independent of channel/bearing
naming conventions specific to IMS.
"""
import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.signal import butter, hilbert, sosfiltfilt
from scipy.stats import kurtosis, skew


def time_domain_features(signal: np.ndarray) -> dict:
    x = signal.astype(np.float64)
    rms = np.sqrt(np.mean(x ** 2))
    peak = np.max(np.abs(x))
    mean_abs = np.mean(np.abs(x))
    root_amplitude = np.mean(np.sqrt(np.abs(x))) ** 2
    return {
        "mean": np.mean(x),
        "median": np.median(x),
        "std": np.std(x),
        "rms": rms,
        "peak": peak,
        "peak_to_peak": x.max() - x.min(),
        "kurtosis": kurtosis(x, fisher=True),
        "skewness": skew(x),
        "crest_factor": peak / rms if rms > 0 else 0.0,
        "shape_factor": rms / mean_abs if mean_abs > 0 else 0.0,
        "impulse_factor": peak / mean_abs if mean_abs > 0 else 0.0,
        "clearance_factor": peak / root_amplitude if root_amplitude > 0 else 0.0,
        "zero_crossing_rate": np.mean(np.diff(np.signbit(x)).astype(np.float64)),
        "q05": np.quantile(x, 0.05),
        "q95": np.quantile(x, 0.95),
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
    spectral_variance = np.sum(((freqs - spectral_centroid) ** 2) * power) / total_power
    probability = power / total_power
    nonzero_probability = probability[probability > 0]
    entropy_denominator = np.log(max(len(probability), 2))
    spectral_entropy = -np.sum(nonzero_probability * np.log(nonzero_probability)) / entropy_denominator

    third = len(freqs) // 3
    low_energy = power[:third].sum() / total_power
    mid_energy = power[third:2 * third].sum() / total_power
    high_energy = power[2 * third:].sum() / total_power

    features = {
        "dominant_freq": dominant_freq,
        "spectral_centroid": spectral_centroid,
        "spectral_spread": np.sqrt(max(spectral_variance, 0.0)),
        "spectral_entropy": spectral_entropy,
        "spectral_energy": power.sum(),
        "low_band_energy_ratio": low_energy,
        "mid_band_energy_ratio": mid_energy,
        "high_band_energy_ratio": high_energy,
    }
    for low_hz, high_hz in (
        (0, 500), (500, 1000), (1000, 2000),
        (2000, 5000), (5000, 10000), (10000, 15000),
    ):
        band = (freqs >= low_hz) & (freqs < high_hz)
        features[f"energy_{low_hz}_{high_hz}_hz_ratio"] = (
            float(power[band].sum() / total_power) if band.any() else 0.0
        )
    return features


def envelope_features(signal: np.ndarray, sampling_rate_hz: float) -> dict:
    """Features from a resonance-band Hilbert envelope for bearing impacts."""
    x = signal.astype(np.float64) - np.mean(signal)
    nyquist = sampling_rate_hz / 2.0
    if len(x) >= 64 and nyquist > 2_500:
        upper = min(10_000.0, nyquist * 0.9)
        if upper > 2_000:
            sos = butter(4, [2_000.0, upper], btype="bandpass", fs=sampling_rate_hz, output="sos")
            x = sosfiltfilt(sos, x)
    envelope = np.abs(hilbert(x))
    envelope_rms = float(np.sqrt(np.mean(envelope ** 2)))
    envelope_peak = float(np.max(envelope))
    centered = envelope - np.mean(envelope)
    frequencies = rfftfreq(len(centered), d=1 / sampling_rate_hz)
    power = np.abs(rfft(centered)) ** 2
    total_power = float(power.sum()) if power.sum() > 0 else 1e-12
    features = {
        "envelope_rms": envelope_rms,
        "envelope_std": float(np.std(envelope)),
        "envelope_peak": envelope_peak,
        "envelope_kurtosis": float(kurtosis(envelope, fisher=True)),
        "envelope_crest_factor": envelope_peak / envelope_rms if envelope_rms > 0 else 0.0,
    }
    for low_hz, high_hz in ((0, 50), (50, 100), (100, 200), (200, 500), (500, 1000)):
        band = (frequencies >= low_hz) & (frequencies < high_hz)
        features[f"envelope_energy_{low_hz}_{high_hz}_hz_ratio"] = (
            float(power[band].sum() / total_power) if band.any() else 0.0
        )
    return features


def extract_window_features(signal: np.ndarray, sampling_rate_hz: float) -> dict:
    """Extract the full feature set for one vibration signal window."""
    return {
        **time_domain_features(signal),
        **freq_domain_features(signal, sampling_rate_hz),
        **envelope_features(signal, sampling_rate_hz),
    }


VIBRATION_FEATURE_COLUMNS = [
    "mean", "median", "std", "rms", "peak", "peak_to_peak", "kurtosis", "skewness",
    "crest_factor", "shape_factor", "impulse_factor", "clearance_factor",
    "zero_crossing_rate", "q05", "q95", "dominant_freq", "spectral_centroid",
    "spectral_spread", "spectral_entropy", "spectral_energy", "low_band_energy_ratio",
    "mid_band_energy_ratio", "high_band_energy_ratio", "energy_0_500_hz_ratio",
    "energy_500_1000_hz_ratio", "energy_1000_2000_hz_ratio",
    "energy_2000_5000_hz_ratio", "energy_5000_10000_hz_ratio",
    "energy_10000_15000_hz_ratio", "envelope_rms", "envelope_std", "envelope_peak",
    "envelope_kurtosis", "envelope_crest_factor", "envelope_energy_0_50_hz_ratio",
    "envelope_energy_50_100_hz_ratio", "envelope_energy_100_200_hz_ratio",
    "envelope_energy_200_500_hz_ratio", "envelope_energy_500_1000_hz_ratio",
]
