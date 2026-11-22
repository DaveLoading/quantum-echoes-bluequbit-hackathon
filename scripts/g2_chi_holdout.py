"""G2 MPS bond-dimension hold-out test and chi-to-infinity file. Local only, no BlueQubit calls,
no site scores used.

Hold-out: for each qubit outside q22-25, predict its chi 24 value from the lower-chi runs
(chi 18 to 23, whatever has finished) with several forms, and compare with the measured chi 24.
Then build a file with the best form, using ALL chi values up to 24, and take q22-25 from the
uploaded 540 file (Pauli-path there).

Usage: python3 work/g2_chi_holdout.py [out.csv]
"""
import csv
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import bq_kit  # noqa: E402

BASE = os.path.join(ROOT, "work", "csv", "G2__mps-chi24_pp-level0-1e-4-q22-25.csv")
PP_Q = (22, 23, 24, 25)
V = {}
for r in bq_kit.ledger_rows():
    if r["circuit"] == "G2" and r["tag"].startswith("mps.cpu_chi") and r.get("value") is not None and r.get("status") == "COMPLETED":
        V.setdefault(int(r["tag"].split("chi")[1]), {})[r["q"]] = r["value"]
base = {int(x[0].rsplit("_", 1)[1]): float(x[1]) for x in csv.reader(open(BASE)) if x[0] != "circuit_id"}
mq = [q for q in range(36) if q not in PP_Q]
low = [c for c in sorted(V) if 18 <= c <= 23]
print("chi sets:", {c: len(V[c]) for c in sorted(V)})


def series(q, chis):
    return [(c, V[c][q]) for c in chis if q in V.get(c, {})]


def predict(form, pts, target):
    """pts: [(chi, value)]; target: a chi value or np.inf."""
    if not pts:
        return None
    x = np.array([p[0] for p in pts], float)
    y = np.array([p[1] for p in pts], float)
    if form == "mean":
        return float(np.mean(y))
    if form == "last":
        return float(y[np.argmax(x)])
    if len(pts) < 2:
        return float(y[-1])
    if form == "lin_1/chi":
        k = np.polyfit(1 / x, y, 1)
        return float(np.polyval(k, 0.0 if target == np.inf else 1 / target))
    if form == "lin_chi":
        if target == np.inf:
            return None
        k = np.polyfit(x, y, 1)
        return float(np.polyval(k, target))
    raise ValueError(form)


forms = ["last", "mean", "lin_1/chi", "lin_chi"]
err = {f: [] for f in forms}
for q in mq:
    if q not in V.get(24, {}):
        continue
    pts = series(q, low)
    if len(pts) < 2:
        continue
    for f in forms:
        p = predict(f, pts, 24)
        if p is not None:
            err[f].append(p - V[24][q])
print(f"hold-out on chi 24 from chi {low} ({len(err['last'])} qubits):")
for f in forms:
    e = np.array(err[f])
    if len(e):
        print(f"  {f:10s} rms {np.sqrt(np.mean(e ** 2)):.4f}  mean {np.mean(e):+.4f}")

if len(sys.argv) > 1:
    best = min((f for f in ("mean", "lin_1/chi") if err[f]), key=lambda f: np.sqrt(np.mean(np.square(err[f]))))
    allc = [c for c in sorted(V) if 18 <= c <= 24]
    vals = []
    for q in range(36):
        if q in PP_Q:
            vals.append(base[q])
        else:
            vals.append(predict(best, series(q, allc), np.inf))
    out = sys.argv[1]
    bq_kit.write_csv(os.path.join(ROOT, out) if not os.path.isabs(out) else out, "G2", vals)
    a = np.array(vals)
    b = np.array([base[q] for q in range(36)])
    print(f"wrote {out} with form '{best}' over chi {allc}; corr2 vs the 540 file {np.corrcoef(a, b)[0, 1] ** 2:.4f}")
