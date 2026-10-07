"""Score the dummy references, R0 and R1 with the frozen evaluator (PROTOCOL 4.4, red-team item 20).
Writes experiments/xjtu/v2/results/dummies/{summary.csv, per_bearing.csv, latency_floor.csv}.
Run from repo root: python experiments/xjtu/v2/run_dummies.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import causal as c  # noqa: E402

OUT = c.V2 / "results" / "dummies"
OUT.mkdir(parents=True, exist_ok=True)

feats = c.load_features(); gt = c.load_gt()
refs = {"R0": c.states_r(pd.read_csv(c.ROOT / "experiments/xjtu/results/baseline_oof.csv")),
        "R1": c.states_r(pd.read_csv(c.ROOT / "experiments/xjtu/results/combined_oof.csv"))}
refs.update(c.dummy_states(feats, k=3))

summ, perb = [], []
for name, st in refs.items():
    s = c.score(st, feats, gt)
    row = dict(model=name, C=s.C.mean(), C1=s.C1.mean(), LC_sum=s.LC.sum(), MC=s.MC.sum(), EP=s.EP.sum(),
               ECh=s.ECh.mean(), FD=s.FD.sum(), FDh=s.FDh.mean(), LD=s.LD.mean(), EFh=s.EFh.mean(),
               MO=s.MO.sum())
    for w in c.W_EP_SENS:
        row[f"C_wEP{int(w)}"] = c.score(st, feats, gt, w_ep=w).C.mean()
    for v in ("env", "hf", "twophase", "v2x"):
        row[f"C_{v}"] = c.score(st, feats, gt, variant=v).C.mean()
    row["C_drop31_32"] = s.C.drop(["3_1", "3_2"]).mean()
    row["crit_entry_bins"] = s.crit_lead.map(c.entry_bin).value_counts().to_dict()
    row["early_pages"] = ",".join(f"{b}@{int(v)}" for b, v in s.crit_lead.items() if v > 60)
    summ.append(row)
    s = s.assign(model=name, crit_bin=s.crit_lead.map(c.entry_bin), faulty_bin=s.faulty_lead.map(c.entry_bin))
    perb.append(s.reset_index())

S = pd.DataFrame(summ)
S.to_csv(OUT / "summary.csv", index=False)
pd.concat(perb).to_csv(OUT / "per_bearing.csv", index=False)
pd.concat([c.latency_floor(gt, k) for k in (3, 5)]).to_csv(OUT / "latency_floor.csv")
pd.set_option("display.width", 250)
print(S.round(3).to_string(index=False))
print(pd.concat(perb).pivot(index="bearing", columns="model", values="C").round(2).to_string())
print(pd.concat([c.latency_floor(gt, k) for k in (3, 5)]).to_string())
