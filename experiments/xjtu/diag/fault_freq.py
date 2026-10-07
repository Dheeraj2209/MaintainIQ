"""Envelope-spectrum fault-frequency SNR features for LDK UER204 (diag only)."""
import re, numpy as np, pandas as pd, time
from pathlib import Path
from scipy.signal import butter, sosfiltfilt, hilbert
from scipy.fft import rfft, rfftfreq
from scipy.stats import kurtosis
FS=25600.0; ROOT=Path('local_data/xjtu_full/XJTU-SY_Bearing_Datasets')
dD=7.92/34.55; NB=8
ORD={'FTF':0.5*(1-dD),'BSF':(1/(2*dD))*(1-dD**2),'BPFO':NB/2*(1-dD),'BPFI':NB/2*(1+dD)}
FR={1:35.0,2:37.5,3:40.0}
SOS=butter(4,[2000,10000],btype='bandpass',fs=FS,output='sos')
def num(p): return int(re.search(r'(\d+)$',p.stem).group(1))
def feats(x, fr):
    out={}
    N=len(x); f=rfftfreq(N,1/FS)
    X=np.abs(rfft(x-x.mean()))/N; rms=np.sqrt(np.mean((x-x.mean())**2))
    def peak(spec,fc,tol=0.03):
        m=(f>=fc*(1-tol))&(f<=fc*(1+tol)); return spec[m].max() if m.any() else 0.0
    for k in (1,2,3): out[f'shaft_{k}x_rel']=peak(X,k*fr,0.03)/rms
    # spectral kurtosis-ish: kurtosis of band-filtered signals in 4 bands
    for lo,hi in ((1000,3000),(3000,6000),(6000,9000),(9000,12000)):
        s=sosfiltfilt(butter(4,[lo,hi],btype='bandpass',fs=FS,output='sos'),x); out[f'bandkurt_{lo}_{hi}']=kurtosis(s)
        out[f'bandrms_{lo}_{hi}_rel']=np.sqrt(np.mean(s**2))/rms
    env=np.abs(hilbert(sosfiltfilt(SOS,x))); env=env-env.mean()
    E=np.abs(rfft(env))/N
    noise=np.median(E[(f>5)&(f<400)])+1e-12
    for name,o in ORD.items():
        for k in (1,2,3):
            out[f'env_{name}_{k}x_snr']=peak(E,k*o*fr,0.02)/noise
        out[f'env_{name}_sum_snr']=sum(out[f'env_{name}_{k}x_snr'] for k in (1,2,3))
    out['env_shaft_1x_snr']=peak(E,fr,0.03)/noise
    return out
rows=[]; t=time.time()
for cdir in sorted(ROOT.iterdir()):
    if not cdir.is_dir(): continue
    for bdir in sorted(cdir.iterdir()):
        b=bdir.name; cond=int(b[7]); files=sorted(bdir.glob('*.csv'),key=num); L=len(files)
        idx=range(L) if L<=600 else list(range(20))+list(range(L-300,L))
        for i in idx:
            a=pd.read_csv(files[i]).to_numpy(float)
            r={'bearing_id':b,'condition':cond,'cycle':i,'rul_minutes':L-1-i}
            for ax,col in (('h',0),('v',1)):
                r.update({f'{ax}_{k}':v for k,v in feats(a[:,col],FR[cond]).items()})
            rows.append(r)
        print(b, L, f'{time.time()-t:.0f}s', flush=True)
pd.DataFrame(rows).to_csv('experiments/xjtu/diag/fault_freq_features.csv',index=False)
