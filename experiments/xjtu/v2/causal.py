"""Frozen evaluator for PROTOCOL.md (v2): causal indicators, state-machine replay, cost, statistics.

This module is part of the pre-registration. It is frozen together with PROTOCOL.md and must not
be edited after the freeze, except to fix a crash bug (logged in PROTOCOL.md "Deviations").

Read-only inputs: outputs/xjtu_features.csv, experiments/xjtu/v2/onset_gt.csv,
experiments/xjtu/results/{baseline,combined}_oof.csv.

Conventions
- One "bearing" = one run-to-failure trajectory; rows are one-minute snapshots in cycle order.
- States: 0 healthy, 1 degrading, 2 faulty, 3 critical.
- Warm-up: rows < COMMISSION (20) are always healthy; all run counters start counting at row 20.
- All run counters (stage-1 trigger, faulty, critical) count CONCURRENTLY and independently of
  whether the onset latch O has fired (PROTOCOL 2.2). faulty/critical additionally require O = 1.
"""
from __future__ import annotations

from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
V2 = Path(__file__).resolve().parent
COMMISSION = 20
M_ESC = 3                       # escalation persistence m (fixed)
K_MAX = 5                       # largest stage-1 k in the grid
MEDIAN_LAG = 2                  # lag (rows) of a trailing 5-row median to a step change
MO_MIN_GT_LATE_RUL = 10 + (K_MAX - 1) + MEDIAN_LAG   # = 16 (PROTOCOL 4.1, red-team item 4)

# ---- cost weights (PROTOCOL 4.1; fixed) ---------------------------------------------------
W_LC = 10.0      # graded late/missed critical
W_EP_PRIMARY = 5.0
W_EP_SENS = (3.0, 5.0, 10.0)   # MC:EP exchange-rate sensitivity (10:3, 10:5, 10:10)
W_EC, CAP_EC = 0.5, 4.0        # hours critical while RUL > 60, capped per bearing
W_FD = 0.5                     # false degrading episodes starting before t_early
W_FDH, CAP_FDH = 0.25, 8.0     # hours non-healthy before t_early, capped per bearing
W_LD, CAP_LD = 1.0, 4.0        # onset lateness hours, capped per bearing
W_EF, CAP_EF = 0.25, 4.0       # hours faulty-or-above while RUL > 120, capped per bearing
W_MO = 10.0                    # missed onset (stage 1 only, scorable bearings only)


# ============================================================================ data
def load_features() -> dict[str, pd.DataFrame]:
    cols = ["bearing_id", "cycle", "condition", "rul_minutes", "h_rms", "v_rms", "h_peak", "v_peak"]
    f = pd.read_csv(ROOT / "outputs/xjtu_features.csv", usecols=cols)
    f["b"] = f.bearing_id.str.replace("Bearing", "", regex=False)
    f = f.sort_values(["b", "cycle"])
    return {b: d.reset_index(drop=True) for b, d in f.groupby("b", sort=True)}


def load_gt() -> pd.DataFrame:
    return pd.read_csv(V2 / "onset_gt.csv", dtype={"bearing": str}).set_index("bearing")


def gt_variant_rows(gt: pd.DataFrame, b: str, n: int, variant: str = "primary") -> tuple[int, int]:
    """(t_early_row, t_late_row) for a GT variant. Single-point variants give early == late.
    A variant without an onset for this bearing falls back to the primary interval."""
    g = gt.loc[b]
    if variant == "primary":
        return int(g.gt_early_row), int(g.gt_late_row)
    col = {"env": "env_rul", "hf": "hf_rul", "twophase": "twophase03_rul", "v2x": "perm2x_rul",
           "hinge": "hinge_rul", "on": "gt_on_rul"}[variant]
    v = g[col]
    if pd.isna(v):
        return int(g.gt_early_row), int(g.gt_late_row)
    row = int(n - 1 - int(v))
    return row, row


# ============================================================================ indicators
def trailing_median(x: np.ndarray, w: int = 5) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=1).median().to_numpy()


def reference(x: np.ndarray, mode: str = "self") -> float:
    """Commissioning reference for one channel. 'fallback' = min(med20, m60) when m60 < 0.9*med20,
    m60 = minimum trailing-10 median over rows 0..59 (only known once row 59 is seen; before that
    the self reference is used -- handled in indicators())."""
    med20 = float(np.median(x[:COMMISSION]))
    if mode == "self":
        return med20
    m60 = float(pd.Series(x[:60]).rolling(10, min_periods=10).median().min())
    return min(med20, m60) if m60 < 0.9 * med20 else med20


def indicators(d: pd.DataFrame, ref_mode: str = "self") -> dict[str, np.ndarray]:
    """Causal r5, z5, p5 for one bearing (PROTOCOL 2.1)."""
    n = len(d)
    out = {}
    for name, cols in (("r", ("h_rms", "v_rms")), ("p", ("h_peak", "v_peak"))):
        ratios, zs = [], []
        for c in cols:
            x = d[c].to_numpy(float)
            med20 = float(np.median(x[:COMMISSION]))
            mad = float(np.median(np.abs(x[:COMMISSION] - med20)))
            ref = np.full(n, med20)
            if ref_mode == "fallback" and name == "r":
                ref[min(60, n):] = reference(x, "fallback") if n >= 60 else med20
            ratios.append(x / ref)
            zs.append((x - ref) / np.maximum(1.4826 * mad, 0.02 * ref))
        out[name] = np.max(np.vstack(ratios), axis=0)
        if name == "r":
            out["z"] = np.max(np.vstack(zs), axis=0)
    return {"r5": trailing_median(out["r"]), "z5": trailing_median(out["z"]), "p5": trailing_median(out["p"])}


# ============================================================================ state machine
def run_length(b: np.ndarray) -> np.ndarray:
    """Consecutive-True count ending at each row (vectorised)."""
    b = np.asarray(b, bool)
    idx = np.arange(len(b))
    last_false = np.maximum.accumulate(np.where(~b, idx, -1))
    return idx - last_false


def _gate(cond: np.ndarray) -> np.ndarray:
    c = np.asarray(cond, bool).copy()
    c[:COMMISSION] = False
    return c


def replay(trigger: np.ndarray, k: int, faulty_cond=None, crit_cond=None, m: int = M_ESC,
           hysteresis: bool = False, r5: np.ndarray | None = None,
           calm_level: float = 1.1, calm_rows: int = 30) -> np.ndarray:
    """Causal state machine (PROTOCOL 2.2/2.4). Returns int states 0..3.

    trigger: stage-1 per-row trigger. Onset latch O fires at the first row where the trigger
    run (counted from row 20) reaches k. faulty/critical: O = 1 AND their own concurrent run >= m.
    faulty/critical ratchet (never demoted). With hysteresis, degrading clears to healthy after
    calm_rows consecutive rows with r5 < calm_level, only while max_state_reached == degrading;
    after a clear the trigger run must re-accumulate k.
    """
    n = len(trigger)
    trig = _gate(trigger)
    f_ok = run_length(_gate(faulty_cond)) >= m if faulty_cond is not None else np.zeros(n, bool)
    c_ok = run_length(_gate(crit_cond)) >= m if crit_cond is not None else np.zeros(n, bool)
    if not hysteresis:
        O = np.maximum.accumulate(run_length(trig) >= k)
        inst = np.where(O, 1, 0)
        inst = np.where(O & f_ok, 2, inst)
        inst = np.where(O & c_ok, 3, inst)
        esc = np.maximum.accumulate(np.where(inst >= 2, inst, 0))      # ratchet faulty/critical
        return np.maximum(inst, esc).astype(int)
    calm = _gate(r5 < calm_level)
    st = np.zeros(n, int)
    O = False; run = 0; crun = 0; mx = 0
    for i in range(COMMISSION, n):
        run = run + 1 if trig[i] else 0
        crun = crun + 1 if calm[i] else 0
        if not O and run >= k:
            O = True; crun = 0
        elif O and mx < 2 and crun >= calm_rows:      # transient cleared (degrading only)
            O = False; run = 0; crun = 0; mx = 0
        lvl = 0 if not O else (3 if c_ok[i] else (2 if f_ok[i] else 1))
        mx = max(mx, lvl)
        st[i] = max(lvl, mx if mx >= 2 else 0)
    return st


# ============================================================================ cost
def episodes(nonhealthy: np.ndarray) -> list[tuple[int, int]]:
    nh = np.asarray(nonhealthy, bool)
    if not nh.any():
        return []
    d = np.diff(np.r_[0, nh.astype(int), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def bearing_cost(st: np.ndarray, rul: np.ndarray, t_early: int, t_late: int, gt_late_rul: float,
                 w_ep: float = W_EP_PRIMARY) -> dict:
    """Per-bearing composite cost terms (PROTOCOL 4.1). rul = true RUL per row."""
    st = np.asarray(st); rul = np.asarray(rul, float); n = len(st)
    nh = st >= 1
    ci = np.where(st >= 3)[0]
    fi = np.where(st >= 2)[0]
    crit_lead = float(rul[ci[0]]) if len(ci) else np.nan
    faulty_lead = float(rul[fi[0]]) if len(fi) else np.nan
    LC = 1.0 if not len(ci) else max(0.0, 30.0 - crit_lead) / 30.0
    MCev = int((not len(ci)) or crit_lead < 10)             # missed/late critical event
    EP = int(len(ci) > 0 and crit_lead > 60)
    ECh = min(((st >= 3) & (rul > 60)).sum() / 60.0, CAP_EC)
    eps = episodes(nh)
    FD = sum(1 for s, _ in eps if s < t_early)
    FDh = min(nh[:max(t_early, 0)].sum() / 60.0, CAP_FDH)
    after = [s for s, e in eps if e >= t_late]
    t_det = after[0] if after else n
    LD = min(max(0, t_det - t_late) / 60.0, CAP_LD)
    EFh = min(((st >= 2) & (rul > 120)).sum() / 60.0, CAP_EF)
    scorable = gt_late_rul >= MO_MIN_GT_LATE_RUL
    MO = int(scorable and not (nh & (rul >= 10)).any())
    det_lead = float(rul[t_det]) if t_det < n else np.nan
    C = W_LC * LC + w_ep * EP + W_EC * ECh + W_FD * FD + W_FDH * FDh + W_LD * LD + W_EF * EFh
    C1 = W_MO * MO + W_FD * FD + W_FDH * FDh + W_LD * LD
    return dict(C=C, C1=C1, LC=LC, MC=MCev, EP=EP, ECh=ECh, FD=FD, FDh=FDh, LD=LD, EFh=EFh, MO=MO,
                MO_scorable=int(scorable), crit_lead=crit_lead, faulty_lead=faulty_lead,
                det_lead=det_lead, delay=(np.nan if t_det >= n else
                                          (0 if t_early <= t_det <= t_late else
                                           (t_det - t_late if t_det > t_late else t_det - t_early))))


def score(states: dict[str, np.ndarray], feats: dict[str, pd.DataFrame], gt: pd.DataFrame,
          variant: str = "primary", w_ep: float = W_EP_PRIMARY, bearings=None) -> pd.DataFrame:
    rows = []
    for b in (bearings or sorted(states)):
        rul = feats[b].rul_minutes.to_numpy(float)
        te, tl = gt_variant_rows(gt, b, len(rul), variant)
        late_rul = float(rul[tl])
        r = bearing_cost(states[b], rul, te, tl, late_rul, w_ep)
        r["bearing"] = b
        rows.append(r)
    return pd.DataFrame(rows).set_index("bearing")


def entry_bin(lead: float) -> str:
    if np.isnan(lead):
        return "never"
    return "<10 (miss)" if lead < 10 else ("10-60 (useful)" if lead <= 60 else ">60 (early page)")


def latency_floor(gt: pd.DataFrame, k: int, m: int = M_ESC) -> pd.DataFrame:
    """Per-bearing earliest achievable detection/critical, assuming the trigger is true from the GT
    late edge onward (PROTOCOL 4.1 / red-team items 5, 16)."""
    rows = []
    elig = COMMISSION + max(k, m) - 1
    for b, g in gt.iterrows():
        life = int(g.life_min)
        det_row = max(int(g.gt_late_row) + MEDIAN_LAG + k - 1, COMMISSION + k - 1)
        crit_row = max(int(g.gt_late_row) + MEDIAN_LAG + max(k, m) - 1, elig)
        rows.append(dict(bearing=b, k=k, eligible_row=COMMISSION + k - 1, gt_early_row=int(g.gt_early_row),
                         early_before_eligible=int(g.gt_early_row) < COMMISSION + k - 1,
                         best_det_rul=life - 1 - det_row, best_crit_rul=life - 1 - crit_row,
                         MC_unavoidable=(life - 1 - crit_row) < 10,
                         LC_floor=min(1.0, max(0.0, 30.0 - (life - 1 - crit_row)) / 30.0)))
    return pd.DataFrame(rows).set_index("bearing")


# ============================================================================ statistics
BOOT_REPS, BOOT_SEED = 2000, 20261006


def boot_ci(x: np.ndarray, reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> tuple[float, float]:
    x = np.asarray(x, float); rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(reps, len(x)))
    m = x[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def signflip_p(diff: np.ndarray) -> float:
    """Exact one-sided paired sign-flip permutation p-value for H1: mean(diff) < 0.
    Enumerates all 2^n sign patterns (n <= 20)."""
    d = np.asarray(diff, float); n = len(d)
    signs = np.array(list(product([1.0, -1.0], repeat=n)))
    stats = (signs * np.abs(d)).mean(axis=1)
    return float((stats <= d.mean() + 1e-12).mean())


def paired_se(diff: np.ndarray) -> float:
    d = np.asarray(diff, float)
    return float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0


def influence_signs(diff: pd.Series, extra_drop=(("3_1", "3_2"),)) -> dict:
    """Sign of mean paired diff under every single-bearing deletion and the extra drop sets."""
    out = {"full": float(diff.mean())}
    for b in diff.index:
        out[f"-{b}"] = float(diff.drop(b).mean())
    for s in extra_drop:
        out["-" + "-".join(s)] = float(diff.drop(list(s), errors="ignore").mean())
    return out


# ============================================================================ reference states
def states_r(oof: pd.DataFrame) -> dict[str, np.ndarray]:
    """R0/R1 mapping: healthy unless warning; then critical <=30, faulty <=60, degrading <=120."""
    oof = oof.copy(); oof["b"] = oof.bearing_id.str.replace("Bearing", "", regex=False)
    out = {}
    for b, d in oof.sort_values(["b", "cycle"]).groupby("b"):
        w = d.warning.to_numpy(bool); r = d.rul_pred.to_numpy(float)
        s = np.zeros(len(d), int)
        s[w & (r <= 120)] = 1; s[w & (r <= 60)] = 2; s[w & (r <= 30)] = 3
        out[b] = s
    return out


def ratio_trigger(ind: dict, G: float) -> np.ndarray:
    return ind["r5"] >= G


def dummy_states(feats, k: int = 3) -> dict[str, dict[str, np.ndarray]]:
    """Dummy references required by PROTOCOL 4.4 (red-team item 20)."""
    D = {"never": {}, "fire_at_eligibility": {}, "fire_at_eligibility_critical": {},
         "onset_eq_critical": {}, "onset_only": {}}
    for b, d in feats.items():
        n = len(d); elig = COMMISSION + k - 1
        ind = indicators(d, "self")
        D["never"][b] = np.zeros(n, int)
        s = np.zeros(n, int); s[elig:] = 1; D["fire_at_eligibility"][b] = s
        s = np.zeros(n, int); s[COMMISSION + max(k, M_ESC) - 1:] = 3; D["fire_at_eligibility_critical"][b] = s
        on = replay(ratio_trigger(ind, 1.3), k)
        D["onset_only"][b] = on
        D["onset_eq_critical"][b] = np.where(on >= 1, 3, 0)
    return D
