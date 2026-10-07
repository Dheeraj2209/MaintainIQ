import sys,time; sys.path.insert(0,'.')
from threadpoolctl import threadpool_limits
from experiments.xjtu.harness import load_context
from src.training.xjtu_rul import classifier_feature_columns
from xgboost import XGBClassifier
from sklearn.ensemble import ExtraTreesClassifier
c=load_context(); X=c[classifier_feature_columns(c)].fillna(0).to_numpy(); y=c.rul_minutes.to_numpy()<=120
with threadpool_limits(1):
  t=time.time(); XGBClassifier(n_estimators=100,max_depth=3,subsample=0.7,colsample_bytree=0.5,tree_method='hist',n_jobs=1).fit(X,y); print('xgb100',time.time()-t)
  t=time.time(); ExtraTreesClassifier(n_estimators=50,min_samples_leaf=20,max_features=0.3,n_jobs=1).fit(X,y); print('et50 reg',time.time()-t)
  t=time.time(); ExtraTreesClassifier(n_estimators=50,min_samples_leaf=3,max_features=0.8,n_jobs=1).fit(X,y); print('et50 base',time.time()-t)
