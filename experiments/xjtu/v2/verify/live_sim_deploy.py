"""Skeptic check: stream held-out bearings one snapshot at a time through an independent
re-implementation of INTEGRATION_PLAN.md section 3.4, holding only first-20 + last-60 raw rows
+ the section 3.2 state. Compare to the offline outer-fold publication (final_timeline.csv).
Also: restart tests (serialize state, cold instance)."""
import os, sys, json, copy, importlib.util
from collections import deque
from pathlib import Path
os.environ["OMP_NUM_THREADS"] = "3"
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
ARGS = sys.argv[1:]
sys.argv = [sys.argv[0], "--part", "N"]
import numpy as np, pandas as pd
spec = importlib.util.spec_from_file_location("bp", V2 / "build_post-onset-rul.py")
bp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bp)
c, h = bp.c, bp.h
BEARINGS = ARGS or ["2_3", "3_1", "2_2"]
TL = pd.read_csv(V2 / "results/final_timeline.csv", dtype={"bearing": str})
RAW = pd.read_csv(c.ROOT / "outputs/xjtu_features.csv")
RAW["b"] = RAW.bearing_id.str.replace("Bearing", "", regex=False)
COLS = ["h_rms", "v_rms", "h_peak", "v_peak", "h_kurtosis", "v_kurtosis", "h_envelope_rms", "v_envelope_rms"]
SMAP = {"healthy": 0, "degrading": 1, "faulty": 2, "critical": 3}


def med(x):
    return float(np.median(x))


class Live:
    """Per-machine live state. hist: last 60 raw rows; comm: first 20 raw rows; S: section-3.2 state."""

    def __init__(self, art):
        self.a = art; self.t = 0; self.comm = []; self.hist = deque(maxlen=60)
        self.S = dict(O=False, fpt=None, lr_fpt=None, peak=None, trig=0, frun=0, crun=0, mx=0,
                      ref=None, med=None, mad=None, kmed=None, kmad=None, m60=None,
                      w_r=[], w_p=[], w_kz=[], w_lr=[])

    def _ratios(self, row, t):
        S = self.S
        ref = S["ref"] if (t >= 60 and S["m60"]) else S["med"]
        r = max(row[k] / ref[k] for k in ("h_rms", "v_rms"))
        p = max(row[k] / S["med"][k] for k in ("h_peak", "v_peak"))
        kz = max((row[k] - S["kmed"][k]) / max(1.4826 * S["kmad"][k], 0.02 * abs(S["kmed"][k]), 1e-12)
                 for k in ("h_kurtosis", "v_kurtosis"))
        return r, p, kz

    def _push(self, r, p, kz):
        S = self.S
        S["w_r"] = (S["w_r"] + [r])[-5:]; S["w_p"] = (S["w_p"] + [p])[-5:]; S["w_kz"] = (S["w_kz"] + [kz])[-5:]
        r5 = med(S["w_r"]); S["w_lr"] = (S["w_lr"] + [float(np.log(r5))])[-30:]
        return r5, med(S["w_p"]), med(S["w_kz"])

    @staticmethod
    def slope(y, w):
        y = np.asarray(y[-w:], float)
        if len(y) < 2:
            return 0.0
        x = np.arange(len(y), dtype=float)
        return float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))

    def step(self, row):
        a, S, t = self.a, self.S, self.t
        self.hist.append(row)
        if t < 20:
            self.comm.append(row)
        out = dict(t=t, state=0, q50=np.nan, lo=np.nan, hi=np.nan, prob=a["p0"])
        if t == 19:
            S["med"] = {k: med([x[k] for x in self.comm]) for k in COLS}
            S["kmed"] = {k: S["med"][k] for k in ("h_kurtosis", "v_kurtosis")}
            S["kmad"] = {k: med([abs(x[k] - S["kmed"][k]) for x in self.comm]) for k in ("h_kurtosis", "v_kurtosis")}
            for x in self.comm[:-1]:          # retro-compute commissioning windows (rows 0..18)
                self._push(*self._ratios(x, 0))
        if t == 59 and a["ref"] == "fallback":
            rows = list(self.hist)            # rows 0..59 must still be retained here
            S["ref"] = {}
            for k in ("h_rms", "v_rms"):
                xs = np.array([x[k] for x in rows])
                m60 = min(med(xs[i - 9:i + 1]) for i in range(9, 60))
                S["ref"][k] = min(S["med"][k], m60) if m60 < 0.9 * S["med"][k] else S["med"][k]
            S["m60"] = True
        if t < 19:
            self.t += 1
            return out
        r5, p5, kz5 = self._push(*self._ratios(row, t))
        if t < 20:
            self.t += 1
            return out
        lr = S["w_lr"][-1]
        env = max(row[k] / S["med"][k] for k in ("h_envelope_rms", "v_envelope_rms"))
        S["trig"] = S["trig"] + 1 if r5 >= a["G"] else 0
        if not S["O"] and S["trig"] >= a["k"]:
            S["O"] = True; S["fpt"] = t; S["lr_fpt"] = lr; S["peak"] = r5
        if S["O"]:
            S["peak"] = max(S["peak"], r5); f, lrf, pk = S["fpt"], S["lr_fpt"], S["peak"]
        else:
            f, lrf, pk = t, lr, r5
        x = np.array([[t - f, lr, lr - lrf, np.log(pk), self.slope(S["w_lr"], 10), self.slope(S["w_lr"], 30),
                       kz5, np.log(p5), np.log(env)]])
        pred = float(np.expm1(a["model"].predict(x))[0])
        lp = np.log1p(pred); Q = a["Q"]
        vals = {}
        for al in (0.05, 0.1, 0.2):
            vals[al] = np.sort(np.clip([np.expm1(lp + Q[("lo", al)]), pred, np.expm1(lp + Q[("hi", al)])], 0, None))
        q50 = vals[0.1][1]
        S2, S1 = a["S2"], a["S1"]
        fc = (q50 <= 60) or (r5 >= S1) or (S2 is not None and p5 >= S2)
        cc = (q50 <= 30) or (S2 is not None and p5 >= S2 and q50 <= 60)
        S["frun"] = S["frun"] + 1 if fc else 0
        S["crun"] = S["crun"] + 1 if cc else 0
        lvl = 0 if not S["O"] else (3 if S["crun"] >= 3 else (2 if S["frun"] >= 3 else 1))
        S["mx"] = max(S["mx"], lvl)
        st = max(lvl, S["mx"] if S["mx"] >= 2 else 0)
        out["state"] = st
        if st >= 1:
            V = np.array([vals[0.05][0], vals[0.1][0], q50, vals[0.1][2], vals[0.05][2]])[:, None]
            out.update(q50=q50, lo=vals[0.1][0], hi=vals[0.1][2],
                       prob=float(h.prob_within(120.0, (0.05, 0.1, 0.5, 0.9, 0.95), V)[0]))
        self.t += 1
        return out

    def dump_plan_state(self):
        return json.loads(json.dumps(self.S, default=float)), self.t

    @classmethod
    def restore(cls, art, s, t, hist=None):
        o = cls(art); o.S = copy.deepcopy(s); o.t = t
        if hist is not None:
            o.hist = deque(hist, maxlen=60)
        return o


def runs(s, rul):
    out = []; prev = None
    for i, v in enumerate(s):
        if v != prev:
            out.append((int(i), int(rul[i]), int(v))); prev = v
    return out


def main():
    res = {}
    for b in BEARINGS:
        tl = TL[TL.bearing == b].reset_index(drop=True)
        fold = [i for i, bb, _ in bp.FOLDS_DEF if bb == b][0]
        train = [tr for i, bb, tr in bp.FOLDS_DEF if bb == b][0]
        s1key = tl.stage1[0]; ch = tl.fold_choice[0]
        d = [x for x in bp.FULL_GRID if bp.d_name(x) == ch][0]
        assert d["stage2"] == "ET" and d["binning"] == "global" and d["model_crit"] == ("q50", None), ch
        F = bp.FoldStage2(train, bp.spec_by_key(s1key), 42 + fold)
        si = d["stage2_index"]; F.ensure(si)
        p0 = bp.p0_from({i: bp.d_states_for(F, d, i, i)["states"] for i in train}, train)
        pub = bp.outer_publish(F, d, b, p0)
        craw, y, g = F.cal(si, None)
        e = np.log1p(y) - np.log1p(craw[0])
        cf = h.Conformal("global").fit(e, g)
        Q = {}
        for al in (0.05, 0.1, 0.2):
            Q[("lo", al)] = float(cf.quantile(al, np.zeros(1))[0][0])
            Q[("hi", al)] = float(cf.quantile(1 - al, np.zeros(1))[0][0])
        cfg = bp.spec_by_key(s1key)["cfg"]
        art = dict(model=F.refit[si]["m"], Q=Q, p0=p0, G=cfg["G"], k=cfg["k"], ref=cfg["ref"], S1=d["S1"], S2=d["S2"])
        off_st = pub["states"]
        rows = RAW[RAW.b == b].sort_values("cycle")[COLS].to_dict("records")
        L = Live(art); live = [L.step(r) for r in rows]
        lst = np.array([o["state"] for o in live])
        lq = np.array([o["q50"] for o in live]); lp = np.array([o["prob"] for o in live])
        llo = np.array([o["lo"] for o in live])
        tlq = tl.predicted_rul.to_numpy(float)
        r = dict(fold=fold, choice=ch, n=len(rows), p0=p0, p0_timeline=float(tl.failure_within_120_prob[0]),
                 offline_vs_timeline_state_mismatch=int((off_st != tl.state_code.to_numpy()).sum()),
                 offline_vs_timeline_q50_maxabs=float(np.nanmax(np.abs(pub["q50"] - tlq))),
                 live_vs_offline_state_mismatch=int((lst != off_st).sum()),
                 live_vs_offline_q50_maxabs=float(np.nanmax(np.abs(lq - pub["q50"]))),
                 live_vs_offline_lo_maxabs=float(np.nanmax(np.abs(llo - pub["lo"]))),
                 live_vs_offline_prob_maxabs=float(np.nanmax(np.abs(lp - pub["prob"]))),
                 fallback_ref_changed=bool(art["ref"] == "fallback" and L.S["ref"] != {k: L.S["med"][k] for k in ("h_rms", "v_rms")}))
        for tag, tr, keep_hist in (("restart_t40_planstate_only", 40, False),
                                   ("restart_t40_with_60row_history", 40, True),
                                   ("restart_mid_planstate_only", len(rows) // 2, False)):
            L2 = Live(art)
            for rr in rows[:tr]:
                L2.step(rr)
            s, t = L2.dump_plan_state()
            L3 = Live.restore(art, s, t, hist=list(L2.hist) if keep_hist else None)
            try:
                tail = [L3.step(rr)["state"] for rr in rows[tr:]]
                mm = int((np.array(tail) != lst[tr:]).sum()); err = None
            except Exception as ex:
                mm, err = None, f"{type(ex).__name__}: {ex}"
            r[tag] = dict(mismatch=mm, error=err)
        rul = tl.rul_true.to_numpy()
        r["story_live"] = runs(lst, rul)
        r["story_offline_timeline"] = runs(tl.state_code.to_numpy(int), rul)
        r["story_R0"] = runs(tl.R0_state.map(SMAP).to_numpy(), rul)
        r["q50_at_entries"] = {int(i): (None if np.isnan(lq[i]) else round(float(lq[i]), 1)) for i, _, _ in r["story_live"]}
        r["prob_at_entries"] = {int(i): round(float(lp[i]), 3) for i, _, _ in r["story_live"]}
        res[b] = r
        print(b, json.dumps({k: v for k, v in r.items() if not k.startswith("story")}, default=float), flush=True)
        print("  live story (row, RUL, state):", r["story_live"])
        print("  R0 story   (row, RUL, state):", r["story_R0"][:40])
    out = V2 / "verify" / ("live_sim_deploy_" + "_".join(BEARINGS) + ".json")
    out.write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
