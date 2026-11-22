"""Build one CSV for a circuit from finished ledger jobs, taking each qubit's value from the first
setting tag in a preference list that has it. Local only (no BlueQubit calls).

Usage: python3 work/build_combined.py G4 work/csv/G4__pp_1e-4_level0-then-level1.csv \
           pauli-path_level0_th0.0001 pauli-path_level1_th0.0001
Prints which tag supplied each qubit, and refuses to write if any ID is missing.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import bq_kit  # noqa: E402

c, out, tags = sys.argv[1], sys.argv[2], sys.argv[3:]
info = bq_kit.circuits_db().get(c, {})
n_ids = int(info.get("num_ids") or bq_kit.id_count(c, int(info["num_qubits"])))
vals = {t: {} for t in tags}
for r in bq_kit.ledger_rows():
    if r["circuit"] == c and r["tag"] in vals and r.get("status") == "COMPLETED" and r.get("value") is not None:
        vals[r["tag"]][r["q"]] = r["value"]
picked, source = [], {}
for q in range(n_ids):
    t = next((t for t in tags if q in vals[t]), None)
    if t is None:
        sys.exit(f"missing q{q}: no value at any of {tags}. Nothing written.")
    picked.append(vals[t][q])
    source[q] = t
bq_kit.write_csv(os.path.join(ROOT, out) if not os.path.isabs(out) else out, c, picked)
counts = {t: sum(1 for s in source.values() if s == t) for t in tags}
print(f"wrote {out}: {n_ids} IDs; source counts {counts}")
