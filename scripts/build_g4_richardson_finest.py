"""G4 file: per-qubit linear (Richardson order 1) extrapolation to zero truncation from the two
FINEST measured thresholds, which is the 800 file's method with finer input points. Where a qubit
has a point below 7e-5 (3e-5, 4e-5 or 5e-5), the line runs through 7e-5 and that point; otherwise
the qubit keeps the 800 file's line through 1e-4 and 7e-5. Chosen because the hold-out test showed
the slope steepens as the threshold falls, so the finest pair is the better local slope. No site
scores used. Local only. Usage: python3 work/build_g4_richardson_finest.py out.csv [--scale-coarse]
--scale-coarse: qubits with no point below 7e-5 get their 1e-4/7e-5 slope multiplied by the median
fine/coarse slope ratio measured on the qubits that have both, so every qubit gets the same
average depth of correction.
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
def line0(t1, v1, t2, v2):  # value at th = 0 on the line through (t1, v1), (t2, v2)
    return v2 - t2 * (v1 - v2) / (t1 - t2)
def slope(t1, v1, t2, v2):
    return (v2 - v1) / (t2 - t1)
ratios = []
for q in range(36):
    lt = next((t for t in (3e-5, 4e-5, 5e-5) if q in V.get(t, {})), None)
    if lt is not None:
        sc = slope(1e-4, V[1e-4][q], 7e-5, V[7e-5][q])
        if abs(sc) > 1e-9:
            ratios.append(slope(7e-5, V[7e-5][q], lt, V[lt][q]) / sc)
scale = float(np.median(ratios)) if ("--scale-coarse" in sys.argv and ratios) else 1.0
if scale != 1.0:
    print(f"coarse slopes scaled by the median fine/coarse ratio {scale:.2f} (from {len(ratios)} qubits)")
vals, src = [], {}
for q in range(36):
    lowt = next((t for t in (3e-5, 4e-5, 5e-5) if q in V.get(t, {})), None)
    if lowt is not None:
        vals.append(line0(7e-5, V[7e-5][q], lowt, V[lowt][q])); src[q] = f"7e-5 and {lowt:g}"
    else:
        vals.append(V[7e-5][q] - 7e-5 * scale * slope(1e-4, V[1e-4][q], 7e-5, V[7e-5][q]))
        src[q] = "1e-4 and 7e-5" + (f" x{scale:.2f}" if scale != 1.0 else "")
out = [a for a in sys.argv[1:] if not a.startswith("--")][0]
bq_kit.write_csv(os.path.join(ROOT, out) if not os.path.isabs(out) else out, "G4", vals)
from collections import Counter
print(f"wrote {out}; lines through {dict(Counter(src.values()))}")
