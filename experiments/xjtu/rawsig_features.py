"""Physics-based per-snapshot features for XJTU-SY (LDK UER204) - experiment only.

``extract_raw_signal_features(horizontal, vertical, sample_rate_hz, speed_rpm)``
uses ONLY the current 32768-sample window plus the nominal shaft speed (which the
live predictor already receives as ``speed_rpm``). The measured shaft speed is
estimated from the 1x spectral peak searched in [0.90, 1.02] x nominal, and all
bearing defect frequencies are computed as orders of that measured speed.

Every feature is dimensionless (ratio / SNR / kurtosis) so it is per-snapshot
causal and needs no extra state. Deploying these requires extending
``src.ingestion.xjtu_sy.extract_snapshot_features`` to call this function with
``speed_rpm`` (signature change) and rebuilding outputs/xjtu_features.csv.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.fft import rfft, rfftfreq
from scipy.signal import butter, hilbert, sosfiltfilt
from scipy.stats import kurtosis

FS = 25_600.0
# LDK UER204: 8 balls, ball 7.92 mm, pitch 34.55 mm, contact angle 0.
_D_RATIO = 7.92 / 34.55
N_BALLS = 8
ORDERS = {
    "FTF": 0.5 * (1 - _D_RATIO),                       # 0.385
    "BSF": (1 / (2 * _D_RATIO)) * (1 - _D_RATIO ** 2),  # 2.067
    "BPFO": N_BALLS / 2 * (1 - _D_RATIO),              # 3.083
    "BPFI": N_BALLS / 2 * (1 + _D_RATIO),              # 4.917
}
# Real defect lines sit 0.5-4% above the geometric orders (slip / geometry tolerance).
FAULT_TOL = 0.04
BANDS = ((1000, 3000), (3000, 6000), (6000, 9000), (9000, 12000))
_SOS_CACHE: dict = {}


def _sos(lo, hi, fs):
    key = (lo, hi, fs)
    if key not in _SOS_CACHE:
        _SOS_CACHE[key] = butter(4, [lo, hi], btype="bandpass", fs=fs, output="sos")
    return _SOS_CACHE[key]


def _peak(spec, freqs, fc, tol_rel, min_bins=1):
    df = freqs[1] - freqs[0]
    half = max(fc * tol_rel, min_bins * df)
    m = (freqs >= fc - half) & (freqs <= fc + half)
    return float(spec[m].max()) if m.any() else 0.0


def _envelope_spectrum(x_band, n, fs):
    env = np.abs(hilbert(x_band))
    env = env - env.mean()
    return np.abs(rfft(env)) / n


def _fault_snrs(E, freqs, fr, prefix):
    out = {}
    noise = float(np.median(E[(freqs > 5) & (freqs < 500)])) + 1e-12
    for name, order in ORDERS.items():
        tot = 0.0
        for k in (1, 2, 3):
            v = _peak(E, freqs, k * order * fr, FAULT_TOL) / noise
            if prefix == "env":
                out[f"{prefix}_{name}_{k}x_snr"] = v
            tot += v
        out[f"{prefix}_{name}_sum_snr"] = tot
    # BPFI is amplitude-modulated by shaft rotation: +-1x sidebands
    bpfi = ORDERS["BPFI"] * fr
    out[f"{prefix}_BPFI_sb_snr"] = (
        _peak(E, freqs, bpfi - fr, FAULT_TOL) + _peak(E, freqs, bpfi + fr, FAULT_TOL)
    ) / noise
    out[f"{prefix}_shaft_1x_snr"] = _peak(E, freqs, fr, 0.02) / noise
    out[f"{prefix}_fault_max_snr"] = max(out[f"{prefix}_{n}_sum_snr"] for n in ORDERS)
    return out


def measured_shaft_hz(h, v, fs, speed_rpm):
    nominal = speed_rpm / 60.0
    n = len(h)
    freqs = rfftfreq(n, 1 / fs)
    p = np.abs(rfft(h - h.mean())) ** 2 + np.abs(rfft(v - v.mean())) ** 2
    m = (freqs >= 0.90 * nominal) & (freqs <= 1.02 * nominal)
    if not m.any():
        return nominal
    idx = np.flatnonzero(m)
    i = idx[np.argmax(p[idx])]
    # parabolic interpolation on log power for sub-bin precision
    if 0 < i < len(p) - 1:
        a, b, c = np.log(p[i - 1] + 1e-30), np.log(p[i] + 1e-30), np.log(p[i + 1] + 1e-30)
        den = a - 2 * b + c
        delta = 0.5 * (a - c) / den if den != 0 else 0.0
        delta = float(np.clip(delta, -0.5, 0.5))
    else:
        delta = 0.0
    return float((i + delta) * (freqs[1] - freqs[0]))


def axis_features(x, fs, fr):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    n = len(x)
    freqs = rfftfreq(n, 1 / fs)
    X = np.abs(rfft(x)) / n
    rms = float(np.sqrt(np.mean(x ** 2))) + 1e-12
    out = {}
    for k in (1, 2, 3):
        out[f"shaft_{k}x_rel"] = _peak(X, freqs, k * fr, 0.01, 2) * np.sqrt(2) / rms
    band_kurt = {}
    band_sig = {}
    for lo, hi in BANDS:
        s = sosfiltfilt(_sos(lo, hi, fs), x)
        band_sig[(lo, hi)] = s
        band_kurt[(lo, hi)] = float(kurtosis(s))
        out[f"bandkurt_{lo}_{hi}"] = band_kurt[(lo, hi)]
        out[f"bandrms_{lo}_{hi}_rel"] = float(np.sqrt(np.mean(s ** 2))) / rms
    best = max(band_kurt, key=band_kurt.get)
    out["sk_max"] = band_kurt[best]
    out["sk_best_band_center_khz"] = (best[0] + best[1]) / 2000.0
    # fixed resonance band 2-10 kHz (same band as production envelope features)
    E_fixed = _envelope_spectrum(sosfiltfilt(_sos(2000, 10000, fs), x), n, fs)
    out.update(_fault_snrs(E_fixed, freqs, fr, "env"))
    # adaptive band = highest-kurtosis band of the filter bank (kurtogram-lite)
    E_ad = _envelope_spectrum(band_sig[best], n, fs)
    out.update(_fault_snrs(E_ad, freqs, fr, "envsk"))
    env_ad = np.abs(hilbert(band_sig[best]))
    out["envsk_kurtosis"] = float(kurtosis(env_ad))
    return out


def extract_raw_signal_features(horizontal, vertical, sample_rate_hz=FS, speed_rpm=2100.0):
    h = np.asarray(horizontal, dtype=np.float64)
    v = np.asarray(vertical, dtype=np.float64)
    fr = measured_shaft_hz(h, v, sample_rate_hz, speed_rpm)
    feats = {"rs_fr_ratio": fr / (speed_rpm / 60.0)}
    fh = axis_features(h, sample_rate_hz, fr)
    fv = axis_features(v, sample_rate_hz, fr)
    feats.update({f"rs_h_{k}": val for k, val in fh.items()})
    feats.update({f"rs_v_{k}": val for k, val in fv.items()})
    for k in fh:
        if k.endswith("_snr") or k.startswith("shaft_") or k in ("sk_max", "envsk_kurtosis"):
            feats[f"rs_x_{k}"] = max(fh[k], fv[k])
    return {k: float(np.nan_to_num(val, nan=0.0, posinf=1e12, neginf=-1e12)) for k, val in feats.items()}


# ------------------------------------------------------------- batch extraction
DATA_DIR = Path(__file__).resolve().parents[2] / "local_data" / "xjtu_full" / "XJTU-SY_Bearing_Datasets"
SPEED = {1: 2100.0, 2: 2250.0, 3: 2400.0}


def _read(path):
    a = pd.read_csv(path).to_numpy(dtype=np.float64)
    return a[:, 0], a[:, 1]


def extract_one(args):
    bearing_id, cycle, condition, rel_path = args
    h, v = _read(DATA_DIR / rel_path.replace("\\", "/"))
    feats = extract_raw_signal_features(h, v, FS, SPEED[int(condition)])
    return {"bearing_id": bearing_id, "cycle": int(cycle), **feats}
