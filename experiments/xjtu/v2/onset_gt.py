"""Pre-registered offline onset ground truth (PROTOCOL.md section 1).

Reads outputs/xjtu_features.csv only; writes experiments/xjtu/v2/onset_gt.csv.
All quantities use the bearing's FULL trajectory (hindsight) and are never
computed causally or used as features. Run from repo root:
    python experiments/xjtu/v2/onset_gt.py
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "onset_gt.csv"
COMMISSION = 20          # rows the live predictor retains as commissioning baseline
SMOOTH = 5               # centred median (non-causal on purpose)
LOW_FRAC = 0.30          # healthy level = median of lowest 30% of life
T_RATIO = 1.3            # primary exceedance level
PERSIST = 0.90           # fraction of remaining life that must stay above


def healthy_level(x):
    n = len(x)
    return float(np.median(np.sort(x)[: max(5, int(LOW_FRAC * n))]))


def hi(g, cols):
    """max over channels of x / whole-life healthy level (per channel)."""
    return np.max(np.vstack([g[c].to_numpy() / healthy_level(g[c].to_numpy()) for c in cols]), axis=0)


def centred(x):
    return pd.Series(x).rolling(SMOOTH, center=True, min_periods=1).median().to_numpy()


def perm_exceed(h, level):
    """Earliest t with h[t] > level and h > level on >= PERSIST of rows [t, end]."""
    n = len(h); above = h > level
    frac = np.cumsum(above[::-1])[::-1] / (n - np.arange(n))
    cand = np.where(above & (frac >= PERSIST))[0]
    return int(cand[0]) if len(cand) else None


def hinge(h):
    """Least-squares constant-then-linear(+slope) changepoint on log h."""
    y = np.log(np.maximum(h, 1e-9)); n = len(y); idx = np.arange(n, dtype=float)
    best = (np.inf, None)
    for tau in range(3, n - 3):
        X = np.c_[np.ones(n), np.clip(idx - tau, 0, None)]
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        sse = float(((X @ coef - y) ** 2).sum())
        if coef[1] > 0 and sse < best[0]:
            best = (sse, tau)
    return best[1]


def main():
    F = pd.read_csv(ROOT / "outputs" / "xjtu_features.csv").sort_values(["bearing_id", "cycle"]).copy()
    for ax in "hv":
        F[f"{ax}_hf"] = F[f"{ax}_rms"] * np.sqrt(
            F[f"{ax}_energy_5000_10000_hz_ratio"] + F[f"{ax}_energy_10000_15000_hz_ratio"])
    # fleet reference for commissioning sanity: per condition, LOO median of other bearings' first-20 rms
    first20 = F[F.cycle < COMMISSION].groupby("bearing_id")[["h_rms", "v_rms"]].median()
    cond = F.groupby("bearing_id").condition.first()
    p2 = ROOT / "experiments/xjtu/v2/design_learned-onset/onset_delta.csv"
    P2 = pd.read_csv(p2, dtype={"bearing": str}).set_index("bearing")["GT0.3"]
    rows = []
    for b, g in F.groupby("bearing_id", sort=True):
        g = g.reset_index(drop=True); n = len(g); rul = g.rul_minutes.to_numpy()
        r = lambda i: None if i is None else int(rul[i])
        hE = centred(hi(g, ["h_rms", "v_rms"]))
        t_perm = perm_exceed(hE, T_RATIO)
        t_hinge = hinge(hE)
        t_2x = perm_exceed(hE, 2.0)
        t_env = perm_exceed(centred(hi(g, ["h_envelope_rms", "v_envelope_rms"])), T_RATIO)
        t_hf = perm_exceed(centred(hi(g, ["h_hf", "v_hf"])), T_RATIO)
        t_2ph = None
        if b.replace("Bearing", "") in P2.index:
            t_2ph = int(np.argmin(np.abs(rul - P2.loc[b.replace("Bearing", "")])))
        # uncertainty interval = span of four constructions (3 HI families x 2 statistics)
        cands = [t for t in (t_perm, t_env, t_hf, t_2ph) if t is not None]
        t_lo, t_hi = min(cands), max(cands)
        comm = np.max(np.vstack([g[c].to_numpy() / np.median(g[c].to_numpy()[:COMMISSION])
                                 for c in ("h_rms", "v_rms")]), axis=0)
        t_comm = perm_exceed(centred(comm), T_RATIO)
        # baseline quality
        comm_vs_life = float(np.median(hi(g, ["h_rms", "v_rms"])[:COMMISSION]))
        others = first20.drop(index=b)[cond.drop(index=b) == cond[b]]
        fleet = float(np.max(first20.loc[b].to_numpy() / others.median().to_numpy()))
        rows.append(dict(
            bearing=b.replace("Bearing", ""), condition=int(cond[b]), life_min=n,
            gt_on_rul=r(t_perm), gt_early_rul=r(t_lo), gt_late_rul=r(t_hi),
            gt_on_row=t_perm, gt_early_row=t_lo, gt_late_row=t_hi,
            env_rul=r(t_env), hf_rul=r(t_hf), twophase03_rul=r(t_2ph),
            hinge_rul=r(t_hinge), perm2x_rul=r(t_2x),
            commission_perm_rul=r(t_comm),
            pre_onset_rows=t_lo,
            commission_vs_life_level=round(comm_vs_life, 3),
            commission_vs_fleet=round(fleet, 3),
            baseline_contaminated=bool(comm_vs_life > 1.15 or fleet > 1.5),
            short_healthy=bool(t_lo < COMMISSION + 10),
            detector_eligible_row=COMMISSION + 4,
            abrupt=bool(int(rul[t_perm]) <= 30),
        ))
    R = pd.DataFrame(rows)
    R.to_csv(OUT, index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    print(R.to_string(index=False))


if __name__ == "__main__":
    main()
