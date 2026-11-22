import json
rows=[json.loads(l) for l in open('work/jobs.jsonl')]
th=lambda t: float(t.rsplit('_th',1)[1])
tags=sorted({r['tag'] for r in rows if r['circuit']=='G4' and 'level3' in r['tag']},key=lambda t:-th(t))
d={t:{r['q']:r['value'] for r in rows if r['circuit']=='G4' and r['tag']==t and r.get('value') is not None} for t in tags}
print('cost',sum((r.get('cost') or 0) for r in rows),{t:len(d[t]) for t in tags})
for q in range(36): print(q,' '.join(f"{d[t][q]:+.4f}" if q in d[t] else '   -   ' for t in tags))
