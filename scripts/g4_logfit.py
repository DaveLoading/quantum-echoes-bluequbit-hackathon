"""Per-qubit fit v(th) = a + b*ln(th) on G4 pauli-path level 3 values at thresholds <= 1e-4; prints/creates files at a target threshold."""
import json,csv,sys,numpy as np
rows=[json.loads(l) for l in open('work/jobs.jsonl')]
th=lambda t: float(t.rsplit('_th',1)[1])
d={}
for r in rows:
    if r['circuit']=='G4' and 'level3' in r['tag'] and r.get('value') is not None and r['status']=='COMPLETED' and th(r['tag'])<=1.0001e-4:
        d.setdefault(r['q'],{})[th(r['tag'])]=r['value']
def fit(q):
    x=np.log(np.array(sorted(d[q]))); y=np.array([d[q][t] for t in sorted(d[q])])
    b,a=np.polyfit(x,y,1); return a,b
def at(t): return [fit(q)[0]+fit(q)[1]*np.log(t) for q in range(36)]
if __name__=='__main__':
    ref=[float(r[1]) for r in list(csv.reader(open('work/csv/G4__pp-level3_extrap-th0_from-1e-4-7e-5.csv')))[1:]]
    for t in (3e-5,1e-5,3e-6,1e-6,1e-8):
        v=at(t); print('th* %g corr2 vs 800 file %.4f  min %.3f max %.3f'%(t,np.corrcoef(v,ref)[0,1]**2,min(v),max(v)))
    b=[fit(q)[1] for q in range(36)]; print('slope pattern corr2 vs 800 file %.4f'%np.corrcoef(b,ref)[0,1]**2)
    for q in range(36): print(q,len(d[q]),' '.join(f"{x:+.3f}" for x in (at(3e-5)[q],at(1e-5)[q],at(1e-6)[q])), 'meas3e-5' , d[q].get(3e-5))
    if len(sys.argv)>2:
        t=float(sys.argv[1]); v=at(t)
        with open(sys.argv[2],'x',newline='') as f:
            w=csv.writer(f); w.writerow(['circuit_id','value']); w.writerows((f"MQW_G4_{q}",v[q]) for q in range(36))
        print('wrote',sys.argv[2])
