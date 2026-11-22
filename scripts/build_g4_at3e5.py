"""Build a G4 file at a common threshold of 3e-5 (level 3): the measured value where a 3e-5 job
finished, otherwise the log-linear (in th) prediction at 3e-5 from that qubit's 1e-4 and 7e-5
values. Where only 4e-5 or 5e-5 was measured (3e-5 refused), that value is moved to 3e-5 along
the log-linear slope of its 7e-5 and measured points. The log-linear form was picked because it
predicted the held-out 3e-5 / 4e-5 measurements best (rms 0.004, mean bias about 0); no site
scores were used. Local only. Usage: python3 work/build_g4_at3e5.py out.csv
"""
import json, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import bq_kit  # noqa: E402
V = {}
for r in bq_kit.ledger_rows():
    if r["circuit"] == "G4" and r["tag"].startswith("pauli-path_level3_th") and r.get("value") is not None and r.get("status") == "COMPLETED":
        V.setdefault(float(r["tag"].split("_th")[1]), {})[r["q"]] = r["value"]
T = 3e-5
def loglin(t1, v1, t2, v2, t):
    return v2 + (v2 - v1) * (np.log(t) - np.log(t2)) / (np.log(t2) - np.log(t1))
vals, src = [], {}
for q in range(36):
    if q in V.get(3e-5, {}):
        vals.append(V[3e-5][q]); src[q] = "measured 3e-5"
    elif q in V.get(4e-5, {}):
        vals.append(loglin(7e-5, V[7e-5][q], 4e-5, V[4e-5][q], T)); src[q] = "4e-5 measured, log-linear to 3e-5"
    elif q in V.get(5e-5, {}):
        vals.append(loglin(7e-5, V[7e-5][q], 5e-5, V[5e-5][q], T)); src[q] = "5e-5 measured, log-linear to 3e-5"
    else:
        vals.append(loglin(1e-4, V[1e-4][q], 7e-5, V[7e-5][q], T)); src[q] = "log-linear from 1e-4, 7e-5"
out = sys.argv[1]
bq_kit.write_csv(os.path.join(ROOT, out) if not os.path.isabs(out) else out, "G4", vals)
from collections import Counter
print(f"wrote {out}; sources {dict(Counter(src.values()))}")
