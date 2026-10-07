"""Validate the pooled XJTU + FEMTO table against the production context code and the v2 harness.

Checks (all read-only on src/, outputs/, and the frozen v2 files):
 1. combined_features.csv has the XJTU schema; XJTU rows are byte-for-value identical to outputs/.
 2. src.training.xjtu_rul.add_past_context runs on the pooled table, yields finite features, and the
    XJTU rows' context equals add_past_context(XJTU alone) (no cross-bearing leakage).
 3. Onset GT for all 32 bearings with a v3 COPY of the frozen onset_gt.main logic (only the input
    frame and None-handling differ); the 15 XJTU rows must equal the frozen onset_gt.csv exactly.
 4. The frozen evaluator (causal.py) + harness2.evaluate/p1_baselines score the 5 protocol dummies on
    the pooled 32-bearing feats/GT without error.
 5. Per-bearing lifetimes, clean-baseline flags, RMS baseline-ratio trajectory stats, aggregation-mode
    comparison, sanity plot.
Writes experiments/xjtu/v3/results/pooled_validation.json, femto_bearing_summary.csv, rms_ratio_trajectories.png
    python experiments/xjtu/v3/pooled_validate.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
V3 = Path(__file__).resolve().parent
V2 = ROOT / "experiments" / "xjtu" / "v2"
for p in (str(ROOT), str(V2)):
    if p not in sys.path:
        sys.path.insert(0, p)
from src.training.xjtu_rul import add_past_context, classifier_feature_columns  # noqa: E402
import causal as c  # noqa: E402  (frozen; imported, never edited)
import harness2 as h  # noqa: E402
import onset_gt as ogt  # noqa: E402

CACHE, RES = V3 / "cache", V3 / "results"
FEAT_COLS = ["bearing_id", "cycle", "condition", "rul_minutes", "h_rms", "v_rms", "h_peak", "v_peak"]


def pooled_gt(F: pd.DataFrame) -> pd.DataFrame:
    """Copy of onset_gt.main() (frozen) operating on an arbitrary pooled frame. Differences: input
    frame; FEMTO bearings have no two-phase (P2) candidate; t_perm/None-safe 'abrupt'; a bearing with
    no candidate at all gets early=late=n-1 and no_onset=True."""
    F = F.sort_values(["bearing_id", "cycle"]).copy()
    for ax in "hv":
        F[f"{ax}_hf"] = F[f"{ax}_rms"] * np.sqrt(
            F[f"{ax}_energy_5000_10000_hz_ratio"] + F[f"{ax}_energy_10000_15000_hz_ratio"])
    first20 = F[F.cycle < ogt.COMMISSION].groupby("bearing_id")[["h_rms", "v_rms"]].median()
    cond = F.groupby("bearing_id").condition.first()
    P2 = pd.read_csv(V2 / "design_learned-onset/onset_delta.csv", dtype={"bearing": str}).set_index("bearing")["GT0.3"]
    rows = []
    for b, g in F.groupby("bearing_id", sort=True):
        g = g.reset_index(drop=True); n = len(g); rul = g.rul_minutes.to_numpy()
        r = lambda i: None if i is None else int(rul[i])  # noqa: E731
        hE = ogt.centred(ogt.hi(g, ["h_rms", "v_rms"]))
        t_perm = ogt.perm_exceed(hE, ogt.T_RATIO)
        t_hinge = ogt.hinge(hE)
        t_2x = ogt.perm_exceed(hE, 2.0)
        t_env = ogt.perm_exceed(ogt.centred(ogt.hi(g, ["h_envelope_rms", "v_envelope_rms"])), ogt.T_RATIO)
        t_hf = ogt.perm_exceed(ogt.centred(ogt.hi(g, ["h_hf", "v_hf"])), ogt.T_RATIO)
        t_2ph = None
        key = b.replace("Bearing", "")
        if not b.startswith("FEMTO_") and key in P2.index:
            t_2ph = int(np.argmin(np.abs(rul - P2.loc[key])))
        cands = [t for t in (t_perm, t_env, t_hf, t_2ph) if t is not None]
        no_onset = not cands
        t_lo, t_hi = (min(cands), max(cands)) if cands else (n - 1, n - 1)
        comm = np.max(np.vstack([g[col].to_numpy() / np.median(g[col].to_numpy()[:ogt.COMMISSION])
                                 for col in ("h_rms", "v_rms")]), axis=0)
        t_comm = ogt.perm_exceed(ogt.centred(comm), ogt.T_RATIO)
        comm_vs_life = float(np.median(ogt.hi(g, ["h_rms", "v_rms"])[:ogt.COMMISSION]))
        others = first20.drop(index=b)[cond.drop(index=b) == cond[b]]
        fleet = float(np.max(first20.loc[b].to_numpy() / others.median().to_numpy()))
        rows.append(dict(
            bearing=key, condition=int(cond[b]), life_min=n,
            gt_on_rul=r(t_perm), gt_early_rul=r(t_lo), gt_late_rul=r(t_hi),
            gt_on_row=t_perm, gt_early_row=t_lo, gt_late_row=t_hi,
            env_rul=r(t_env), hf_rul=r(t_hf), twophase03_rul=r(t_2ph),
            hinge_rul=r(t_hinge), perm2x_rul=r(t_2x),
            commission_perm_rul=r(t_comm),
            pre_onset_rows=t_lo,
            commission_vs_life_level=round(comm_vs_life, 3),
            commission_vs_fleet=round(fleet, 3),
            baseline_contaminated=bool(comm_vs_life > 1.15 or fleet > 1.5),
            short_healthy=bool(t_lo < ogt.COMMISSION + 10),
            detector_eligible_row=ogt.COMMISSION + 4,
            abrupt=bool(t_perm is not None and int(rul[t_perm]) <= 30),
            no_onset=no_onset,
        ))
    R = pd.DataFrame(rows)
    # round-trip through CSV exactly like the frozen file (dtype parity for the equality check)
    buf = io.StringIO(); R.to_csv(buf, index=False); buf.seek(0)
    return pd.read_csv(buf, dtype={"bearing": str}).set_index("bearing")


def feats_from(P: pd.DataFrame) -> dict[str, pd.DataFrame]:
    f = P[FEAT_COLS].copy()
    f["b"] = f.bearing_id.str.replace("Bearing", "", regex=False)
    f = f.sort_values(["b", "cycle"])
    return {b: d.reset_index(drop=True) for b, d in f.groupby("b", sort=True)}


def healthy_rcv(x: np.ndarray) -> float:
    x = x[:c.COMMISSION]; med = np.median(x)
    return float(1.4826 * np.median(np.abs(x - med)) / med)


def traj_stats(ctx: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for bid, g in ctx.groupby("bearing_id", sort=True):
        g = g.sort_values("cycle")
        r = np.maximum(g.h_rms_baseline_ratio.to_numpy(), g.v_rms_baseline_ratio.to_numpy())
        r5 = pd.Series(r).rolling(5, min_periods=1).median().to_numpy()
        rul = g.rul_minutes.to_numpy(); n = len(g)
        row = dict(bearing_id=bid, n_rows=n, healthy_rcv_h_rms=healthy_rcv(g.h_rms.to_numpy()),
                   healthy_rcv_v_rms=healthy_rcv(g.v_rms.to_numpy()))
        for q in (0.25, 0.5, 0.75, 0.9):
            row[f"r5_at_life{int(q*100)}"] = float(r5[min(n - 1, int(q * n))])
        for m in (120, 60, 30, 10, 0):
            row[f"r5_at_rul{m}"] = float(r5[rul == m][0]) if (rul == m).any() else np.nan
        row["r5_max"] = float(r5.max()); row["r5_pre_last10_max"] = float(r5[:max(1, n - 10)].max())
        row["first_r5_ge_1.3_rul"] = float(rul[np.argmax(r5 >= 1.3)]) if (r5 >= 1.3).any() else np.nan
        row["first_r5_ge_2_rul"] = float(rul[np.argmax(r5 >= 2.0)]) if (r5 >= 2.0).any() else np.nan
        row["max_abs_peak_g"] = float(max(g.h_peak.max(), g.v_peak.max()))
        rows.append(row)
    return pd.DataFrame(rows).set_index("bearing_id")


def mode_comparison() -> dict:
    out = {}
    for mode in ("median6", "last1", "concat6"):
        T = pd.read_csv(CACHE / f"femto_features_{mode}.csv")
        rc, mono, endr = [], [], []
        for _, g in T.groupby("bearing_id"):
            rc.append(healthy_rcv(g.h_rms.to_numpy()))
            x = pd.Series(np.maximum(g.h_rms / np.median(g.h_rms[:20]), g.v_rms / np.median(g.v_rms[:20])))
            # roughness: median |diff| of log ratio over the first 70 % of life (healthy-ish noise)
            lx = np.log(x.to_numpy()[: int(0.7 * len(x))])
            mono.append(float(np.median(np.abs(np.diff(lx)))))
            endr.append(float(x.iloc[-1]))
        out[mode] = dict(healthy_rcv_h_rms_median=float(np.median(rc)),
                         log_ratio_step_noise_median=float(np.median(mono)),
                         end_ratio_median=float(np.median(endr)))
    X = pd.read_csv(ROOT / "outputs/xjtu_features.csv")
    rc, mono = [], []
    for _, g in X.groupby("bearing_id"):
        rc.append(healthy_rcv(g.h_rms.to_numpy()))
        x = np.maximum(g.h_rms / np.median(g.h_rms[:20]), g.v_rms / np.median(g.v_rms[:20])).to_numpy()
        mono.append(float(np.median(np.abs(np.diff(np.log(x[: int(0.7 * len(x))]))))))
    out["xjtu_reference"] = dict(healthy_rcv_h_rms_median=float(np.median(rc)),
                                 log_ratio_step_noise_median=float(np.median(mono)))
    return out


def plot(ctx: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.8))
    for bid, g in ctx.groupby("bearing_id"):
        g = g.sort_values("cycle")
        r = np.maximum(g.h_rms_baseline_ratio, g.v_rms_baseline_ratio)
        r5 = pd.Series(r.to_numpy()).rolling(5, min_periods=1).median()
        fem = bid.startswith("FEMTO_")
        kw = dict(color="#c0392b" if fem else "#2c7fb8", alpha=0.6, lw=0.9)
        ax[0].plot(np.linspace(0, 1, len(g)), r5, **kw)
        m = g.rul_minutes.to_numpy() <= 240
        ax[1].plot(-g.rul_minutes.to_numpy()[m], r5.to_numpy()[m], **kw)
    for a in ax:
        a.set_yscale("log"); a.axhline(1.3, color="k", ls="--", lw=0.7); a.set_ylabel("r5 = med5(max(h,v) rms / baseline)")
    ax[0].set_xlabel("fraction of life"); ax[1].set_xlabel("-RUL (minutes), last 4 h")
    ax[0].set_title("blue: XJTU-SY (15)   red: FEMTO (17, concat6)")
    ax[1].set_title("RMS baseline ratio vs RUL")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def main() -> None:
    RES.mkdir(exist_ok=True)
    X = pd.read_csv(ROOT / "outputs/xjtu_features.csv")
    P = pd.read_csv(CACHE / "combined_features.csv")
    T = pd.read_csv(CACHE / "femto_features.csv")
    out: dict = {}
    # 1 schema
    assert list(P.columns) == list(X.columns)
    Px = P[~P.bearing_id.str.startswith("FEMTO_")].reset_index(drop=True)
    pd.testing.assert_frame_equal(Px, X, check_exact=True)
    out["schema"] = dict(n_columns=len(P.columns), rows_total=len(P), rows_xjtu=len(X), rows_femto=len(T),
                         bearings=int(P.bearing_id.nunique()),
                         conditions=sorted(int(v) for v in P.condition.unique()))
    # 2 add_past_context
    ctx = add_past_context(P)
    ctx_x = add_past_context(X)
    num = ctx.select_dtypes("number")
    out["add_past_context"] = dict(
        n_columns=ctx.shape[1], all_finite=bool(np.isfinite(num.to_numpy()).all()),
        xjtu_rows_identical_to_xjtu_only=bool(np.allclose(
            ctx[~ctx.bearing_id.str.startswith("FEMTO_")].reset_index(drop=True)[ctx_x.columns].select_dtypes("number").to_numpy(),
            ctx_x.select_dtypes("number").to_numpy(), rtol=0, atol=0)),
        n_classifier_feature_columns=len(classifier_feature_columns(ctx)))
    # 3 GT
    gt = pooled_gt(P)
    frozen = c.load_gt()
    gx = gt.loc[frozen.index, frozen.columns]
    try:
        pd.testing.assert_frame_equal(gx, frozen, check_exact=True, check_dtype=False); gt_ok = True  # NaN in FEMTO rows -> float dtype
    except AssertionError as e:  # pragma: no cover
        gt_ok = False; print(e)
    out["gt_xjtu_rows_equal_frozen"] = gt_ok
    out["gt_frozen_files_unchanged"] = h.check_frozen()
    gt_h = gt.drop(columns="no_onset")
    # 4 harness
    feats = feats_from(P)
    D = c.dummy_states(feats, k=3)
    with contextlib.redirect_stdout(io.StringIO()):
        base = h.p1_baselines(feats, gt_h)
    ev = {}
    for name, st in D.items():
        e = h.evaluate(name, st, feats, gt_h, k=3)
        per = e["per_bearing"]
        fem = per.index.str.startswith("FEMTO_")
        ev[name] = dict(C_all=float(per.C.mean()), C_xjtu=float(per.C[~fem].mean()), C_femto=float(per.C[fem].mean()),
                        MC_xjtu=int(per.MC[~fem].sum()), MC_femto=int(per.MC[fem].sum()),
                        EP_femto=int(per.EP[fem].sum()), O3_missed=e["summary"]["O3_missed_onsets"])
    # reference: the same dummies on XJTU only through the untouched frozen loader
    fx, gx0 = c.load_features(), c.load_gt()
    Dx = c.dummy_states(fx, k=3)
    for name in ev:
        ev[name]["C_xjtu_frozen_loader"] = float(c.score(Dx[name], fx, gx0).C.mean())
    out["harness_dummies"] = ev
    out["p1_baselines_ok"] = bool(len(base) == 32)
    # 5 per-bearing summary
    tr = traj_stats(ctx)
    keyed = gt.copy(); keyed.index = ["Bearing" + i if not i.startswith("FEMTO_") else i.replace("FEMTO_", "FEMTO_Bearing") for i in keyed.index]
    summ = tr.join(keyed[["condition", "life_min", "gt_early_rul", "gt_late_rul", "gt_on_rul",
                          "commission_vs_life_level", "commission_vs_fleet", "baseline_contaminated",
                          "short_healthy", "abrupt", "no_onset"]])
    summ["clean_baseline"] = ~summ.baseline_contaminated & ~summ.short_healthy
    summ["dataset"] = np.where(summ.index.str.startswith("FEMTO_"), "FEMTO", "XJTU")
    summ.round(4).to_csv(RES / "pooled_bearing_summary.csv")
    fs = summ[summ.dataset == "FEMTO"]
    xs = summ[summ.dataset == "XJTU"]
    out["femto_lifetimes_min"] = fs.life_min.astype(int).to_dict()
    out["counts"] = {d: dict(n=int(len(s)), life_min_median=float(s.life_min.median()),
                             life_min_range=[int(s.life_min.min()), int(s.life_min.max())],
                             baseline_contaminated=int(s.baseline_contaminated.sum()),
                             short_healthy=int(s.short_healthy.sum()),
                             clean_baseline=int(s.clean_baseline.sum()),
                             clean_bearings=sorted(s.index[s.clean_baseline]),
                             abrupt=int(s.abrupt.sum()), no_onset=int(s.no_onset.sum()),
                             life_ge_120=int((s.life_min > 120).sum()),
                             gt_late_rul_ge_60=int((s.gt_late_rul >= 60).sum()),
                             r5_at_rul60_median=float(s.r5_at_rul60.median()),
                             r5_at_rul30_median=float(s.r5_at_rul30.median()),
                             r5_at_life50_median=float(s.r5_at_life50.median()),
                             healthy_rcv_h_rms_median=float(s.healthy_rcv_h_rms.median()))
                     for d, s in (("FEMTO", fs), ("XJTU", xs))}
    out["aggregation_modes"] = mode_comparison()
    plot(ctx, RES / "rms_ratio_trajectories.png")
    (RES / "pooled_validation.json").write_text(json.dumps(out, indent=1, default=str))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    print(json.dumps({k: v for k, v in out.items() if k != "femto_lifetimes_min"}, indent=1, default=str))
    print(summ[["dataset", "condition", "life_min", "gt_early_rul", "gt_late_rul", "commission_vs_life_level",
                "commission_vs_fleet", "clean_baseline", "abrupt", "healthy_rcv_h_rms", "r5_at_life50",
                "r5_at_rul120", "r5_at_rul60", "r5_at_rul30", "r5_max", "first_r5_ge_1.3_rul", "max_abs_peak_g"]]
          .round(3).to_string())


if __name__ == "__main__":
    main()
