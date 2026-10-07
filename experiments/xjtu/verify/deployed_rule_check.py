import sys; sys.path.insert(0, '.')
import numpy as np
from experiments.xjtu import combined as C
for ss in ("s0", "s1", "s2"):
    p = C.load_probs("et_reg", ss)
    fam, prm = C.RULES[[C.rname(i) for i in range(len(C.RULES))].index("latch(w=3,T=0.6,P=3)")]
    m = np.zeros(len(C.Y), bool)
    for b in C.BEARINGS: m[C.IDX[b]] = C.rule_mask(fam, prm, p[C.IDX[b]])
    fa = {b: int((m[C.IDX[b]] & ~C.TRUTH[C.IDX[b]]).sum()) for b in C.BEARINGS}
    fw = {b: (float(C.Y[C.IDX[b]][m[C.IDX[b]]][0]) if m[C.IDX[b]].any() else None) for b in C.BEARINGS}
    print(ss, "deployed latch FA", sum(fa.values()), "miss", int((~m & C.TRUTH).sum()))
    print("  FA/bearing", {k: v for k, v in fa.items() if v})
    print("  first warn", {k: v for k, v in fw.items() if v is None or v > 120})
