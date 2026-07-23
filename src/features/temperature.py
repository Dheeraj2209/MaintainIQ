"""Synthetic temperature feature generation.

The IMS Bearing Dataset (research/ims_bearing_baseline) has no temperature
channel, but the design baseline (design/DESIGN_BASELINE.md) requires a
temperature reading for every machine. Bearing wear produces frictional
heating, so a monotonic function of vibration RMS energy is a physically
plausible stand-in for a measured signal.

IMPORTANT: this is derived from the raw h_rms feature, NOT from the
health_score/stage label computed in src/prediction/rule_based.py. Deriving
it from the label would leak the target directly into a model feature and
make any classifier trained on it meaningless. RMS is a legitimate model
input, so this makes temperature correlated with another input feature
(realistic — heat and vibration energy are physically linked) rather than a
copy of the answer.

Every value produced here MUST be tagged as synthetic in the output schema
(see ingestion module) — this is a documented limitation, not a claim of
measured data.
"""
import numpy as np
import pandas as pd

BASELINE_TEMP_C = 35.0
MAX_TEMP_RISE_C = 40.0
NOISE_STD_C = 0.5


def synthesize_temperature(rms: pd.Series, baseline_rms: float, random_seed: int = 42) -> pd.Series:
    """Map vibration RMS energy (relative to a healthy baseline) to a plausible
    temperature in Celsius.

    `baseline_rms` should come from each unit's own healthy-period RMS (e.g.
    the same first-10%-of-trajectory window used for health scoring), so the
    curve reflects relative wear per unit rather than an absolute vibration
    magnitude that varies across rigs/sensors.
    """
    rng = np.random.default_rng(random_seed)
    relative_energy = (rms / max(baseline_rms, 1e-9)).clip(lower=1.0) - 1.0
    normalized_rise = 1 - np.exp(-relative_energy)
    noise = rng.normal(0, NOISE_STD_C, size=len(rms))
    return BASELINE_TEMP_C + MAX_TEMP_RISE_C * normalized_rise + noise
