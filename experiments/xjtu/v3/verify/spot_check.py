import sys, numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(r"C:/projects/MaintainIQ"); sys.path.insert(0, str(ROOT))
from src.ingestion.xjtu_sy import extract_snapshot_features
D = ROOT/"local_data/femto/extracted"
P = pd.read_csv(ROOT/"experiments/xjtu/v3/cache/combined_features.csv")
X = pd.read_csv(ROOT/"outputs/xjtu_features.csv")
fc = [c for c in X.columns if c not in ["bearing_id","condition","cycle","elapsed_minutes","rul_minutes","speed_rpm","load_kn","source_file"]]
# global checks
ids = P.bearing_id.unique(); print("n bearings", len(ids), "keys after strip", len(set(i.replace("Bearing","") for i in ids)))
xp = P[~P.bearing_id.str.startswith("FEMTO_")].reset_index(drop=True)
print("XJTU rows equal:", xp.shape==X.shape, np.allclose(xp[fc].to_numpy(), X[fc].to_numpy(), rtol=0, atol=0), (xp.rul_minutes==X.rul_minutes).all())
def rd(p):
    first=open(p).readline(); a=np.loadtxt(p, delimiter=";" if ";" in first else ",", ndmin=2); return a[:,4],a[:,5]
pub = {"1_1":2803,"1_2":871,"2_1":911,"2_2":797,"3_1":515,"3_2":1637,"1_3":2375,"1_4":1428,"1_5":2463,"1_6":2448,"1_7":2259,"2_3":1955,"2_4":751,"2_5":2311,"2_6":701,"2_7":230,"3_3":434}
F = P[P.bearing_id.str.startswith("FEMTO_")]
for k,n in pub.items():
    g = F[F.bearing_id==f"FEMTO_Bearing{k}"]
    assert len(g)==n//6 and g.rul_minutes.iloc[-1]==0 and g.rul_minutes.iloc[0]==n//6-1, k
print("all 17 FEMTO row counts == floor(published/6), RUL end 0")
for split,k in [("Full_Test_Set","1_3"),("Full_Test_Set","1_4"),("Full_Test_Set","2_7")]:
    d = D/split/f"Bearing{k}"; n = len(list(d.glob("acc_*.csv"))); assert n==pub[k]
    g = F[F.bearing_id==f"FEMTO_Bearing{k}"].sort_values("cycle").reset_index(drop=True)
    nm = n//6; start = n-nm*6
    for m in sorted({0, nm//2, nm-2, nm-1}):
        idx = range(start+6*m, start+6*m+6)
        sig = [rd(d/f"acc_{i+1:05d}.csv") for i in idx]
        f = extract_snapshot_features(np.concatenate([s[0] for s in sig]), np.concatenate([s[1] for s in sig]))
        row = g.iloc[m]
        err = max(abs(f[c]-row[c])/(abs(f[c])+1e-9) for c in fc)
        print(k, "min",m,"rul",row.rul_minutes,"files",row.source_file,"max rel err",f"{err:.2e}", "last file idx", idx[-1]+1)
    # causality: last row's last file must be acc_n
    assert g.source_file.iloc[-1].endswith(f"acc_{n:05d}.csv")
print("speed/load ranges:", P.groupby(P.bearing_id.str.startswith("FEMTO_"))[["speed_rpm","load_kn"]].agg(["min","max"]).to_string())
