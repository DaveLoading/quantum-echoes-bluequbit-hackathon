"""Build one CSV per circuit from the ledger, taking each qubit's value from the first tag
in a priority list that has a finished job for it (finest setting first).

    python3 work/build_mixed.py G3 out_name tag1 tag2 ...

Writes work/csv/<circuit>__<out_name>.csv and prints which tag each qubit came from.
Local only: reads work/jobs.jsonl, makes no BlueQubit calls.
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(circuit, out_name, tags):
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    prefix = cfg["prefix"]
    rows = [json.loads(line) for line in open(os.path.join(ROOT, "work", "jobs.jsonl")) if line.strip()]
    by_tag = {t: {} for t in tags}
    for r in rows:
        if r["circuit"] == circuit and r["tag"] in by_tag and r.get("status") == "COMPLETED" and r.get("value") is not None:
            by_tag[r["tag"]][r["q"]] = float(r["value"])
    n_ids = int(cfg.get("ids", {}).get(circuit, 0)) or max(max(d) for d in by_tag.values() if d) + 1
    out, missing, used = [], [], {}
    for q in range(n_ids):
        for t in tags:
            if q in by_tag[t]:
                out.append((f"{prefix}_{circuit}_{q}", by_tag[t][q]))
                used.setdefault(t, []).append(q)
                break
        else:
            missing.append(q)
    if missing:
        sys.exit(f"missing qubits {missing}: no file written")
    path = os.path.join(ROOT, "work", "csv", f"{circuit}__{out_name}.csv")
    if os.path.exists(path):
        sys.exit(f"{path} exists: pick a new out_name")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["circuit_id", "value"])
        w.writerows(out)
    for t in tags:
        print(f"{t}: {len(used.get(t, []))} qubits {used.get(t, [])}")
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
