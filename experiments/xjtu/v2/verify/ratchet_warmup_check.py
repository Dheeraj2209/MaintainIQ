"""Scratch (read-only inputs): R0 vs R0+ratchet vs R0+ratchet+warm-up gate (rows < 20 healthy).
Reuses harness2.evaluate exactly as final.py's FD7 block does. Writes nothing."""
import sys
from pathlib import Path
import numpy as np
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2))
import causal as c          # noqa: E402
import harness2 as h        # noqa: E402

FEATS = c.load_features(); GT = c.load_gt(); BASE = h.p1_baselines(FEATS, GT)
inp = h.reference_inputs(c.ROOT / "experiments/xjtu/results/baseline_oof.csv")

def ratchet(st, warm=0):
    st = np.asarray(st, int).copy(); st[:warm] = 0
    esc = np.maximum.accumulate(np.where(st >= 2, st, 0))
    return np.maximum(st, esc)

for nm, fn in (("R0", lambda s: np.asarray(s, int)), ("R0_ratchet", ratchet),
               ("R0_ratchet_warm20", lambda s: ratchet(s, 20)), ("R0_warm20_only", lambda s: np.where(np.arange(len(s)) < 20, 0, s))):
    st = {b: fn(inp["states"][b]) for b in sorted(FEATS)}
    e = h.evaluate(nm, st, FEATS, GT, k=None, rul_pred=inp["rul_pred"], q_lo=inp["q_lo"], q_hi=inp["q_hi"],
                   prob=inp["prob"], baselines=BASE)
    t = h.table_row(e); p = e["per_bearing"]
    print(nm, "C_w5=%.3f MC=%s EP=%s demotions=%s" % (t["C_w5"], t["MC"], t["EP"], int(p.demotions.sum())),
          "EP_b=", e["summary"]["EP_bearings"], "MC_b=", e["summary"]["MC_bearings"],
          "demote_b=", sorted(p.index[p.demotions > 0]))
for b in sorted(FEATS):
    s = np.asarray(inp["states"][b]); w = s[:20]
    if w.any():
        print("warm-up nonhealthy", b, "states<20:", w.tolist(), "max after 20 first idx>=2:",
              int(np.argmax(s[20:] >= 2)) + 20 if (s[20:] >= 2).any() else None, "len", len(s))
