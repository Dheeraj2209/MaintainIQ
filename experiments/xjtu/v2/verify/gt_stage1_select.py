"""Does nested stage-1 selection (C1, paired 1-SE, E2 stage-1) change under alternative onset GTs?"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
V2 = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(V2))
import causal as c, harness2 as h
FEATS = c.load_features(); GT = c.load_gt(); BS = sorted(FEATS)
AL = pd.read_csv(Path(__file__).with_name("gt_sensitivity_onsets.csv"), index_col=0, dtype={0: str})
AL.index = AL.index.astype(str)
def rows_for(b, v):
    if v == "primary" or pd.isna(AL.at[b, v]):
        return int(GT.at[b, "gt_early_row"]), int(GT.at[b, "gt_late_row"])
    return int(AL.at[b, v]), int(AL.at[b, v])
def frame(st, v):
    rows = []
    for b in BS:
        rul = FEATS[b].rul_minutes.to_numpy(float); te, tl = rows_for(b, v)
        r = c.bearing_cost(st[b], rul, te, tl, float(rul[tl])); r["bearing"] = b; rows.append(r)
    return pd.DataFrame(rows).set_index("bearing")
IND = {ref: {b: c.indicators(FEATS[b], ref) for b in BS} for ref in h.REFS}
G1 = h.stage1_grid()
ST1 = {cfg["name"]: {b: c.replay(h.stage1_trigger(cfg, IND[cfg["ref"]][b]), cfg["k"]) for b in BS} for cfg in G1}
DUM = c.dummy_states(FEATS, 3)
for v in ["primary", "E1p15", "E1p5", "KURT1p3", "SPEC1p15", "RUL120", "RUL60"]:
    fr = {nm: frame(st, v) for nm, st in ST1.items()}
    dfr = {nm: frame(st, v) for nm, st in DUM.items()}
    picks = []; nelig = []
    for _, b, tr in h.outer_folds(BS):
        cands = []
        for cfg in G1:
            el = h.eligibility(fr[cfg["name"]], None, dfr, tr, stage=1)
            cands.append(dict(per_bearing=fr[cfg["name"]].loc[tr], eligible=el["eligible"], complexity=cfg["complexity"],
                              grid_index=cfg["grid_index"], name=cfg["name"]))
        s = h.select(cands, "C1", fallback=dict(name="D(fallback)"))
        picks.append(s["choice"]["name"]); nelig.append(s["n_eligible"])
    best = min(fr, key=lambda k: fr[k].C1.mean())
    print(f"{v:9s} folds eligible(min..max)={min(nelig)}..{max(nelig)} picks={pd.Series(picks).value_counts().to_dict()}"
          f" | all-15 best C1={best} {fr[best].C1.mean():.3f}; D-stage1 C1={fr['ratio1.3/k3/self'].C1.mean():.3f};"
          f" fire_at_elig C1={dfr['fire_at_eligibility'].C1.mean():.3f}")
