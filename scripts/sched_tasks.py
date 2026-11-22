"""Scheduler helper: for each task line, list the next qubits still needed (one per output line).

A task line is what follows `python kit/bq_kit.py run`, e.g.
    G3 --device pauli-path --opt th=1e-4
Output lines: `<run args without --qubits>|<q>`, at most PER_PASS qubits per task, in task order.
Done = COMPLETED with a value. In flight = not dead and no value yet (the kit holds them).
Skipped = 2 or more dead attempts at this setting, or listed in work/rejected.txt
(BlueQubit refused the setting for that qubit, e.g. FAILED_VALIDATION).
Prints ALLDONE when nothing is left or in flight. Reads only local files; no BlueQubit calls.
"""
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import bq_kit  # noqa: E402  (import only; nothing runs on import)

PER_PASS = int(os.environ.get("PER_PASS", "5"))
REJECTED = os.path.join(ROOT, "work", "rejected.txt")


def ids_for(c):
    info = bq_kit.circuits_db().get(c, {})
    if info.get("num_ids"):
        return int(info["num_ids"])
    return bq_kit.id_count(c, int(info["num_qubits"]))


def rejected():
    out = set()
    if os.path.exists(REJECTED):
        for line in open(REJECTED):
            p = line.split()
            if len(p) == 3:
                out.add((p[0], p[1], int(p[2])))
    return out


def main(path):
    rows = bq_kit.ledger_rows()
    rej = rejected()
    out, inflight_total = [], 0
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        a = shlex.split(line)
        c = a[0]
        device = a[a.index("--device") + 1]
        opts = [a[i + 1] for i, x in enumerate(a) if x == "--opt"]
        spec = a[a.index("--qubits") + 1] if "--qubits" in a else None
        tag = bq_kit.tag_for(device, bq_kit.parse_opts(opts))
        target = bq_kit._qubit_list(spec, ids_for(c))
        mine = [r for r in rows if r["circuit"] == c and r["tag"] == tag]
        done = {r["q"] for r in mine if r.get("status") == "COMPLETED" and r.get("value") is not None}
        inflight = {r["q"] for r in mine if not bq_kit._dead(r.get("status")) and r.get("value") is None} - done
        dead = {}
        for r in mine:
            if bq_kit._dead(r.get("status")):
                dead[r["q"]] = dead.get(r["q"], 0) + 1
        skipped = ({q for q, k in dead.items() if k >= 2} | {q for (cc, t, q) in rej if cc == c and t == tag}) - done
        left = [q for q in target if q not in done and q not in inflight and q not in skipped]
        n_in = len([q for q in target if q in inflight])
        inflight_total += n_in
        base = " ".join(x for i, x in enumerate(a) if not (x == "--qubits" or (i > 0 and a[i - 1] == "--qubits")))
        if left:
            out.append(f"{base}|{','.join(map(str, left[:PER_PASS]))}")
        print(f"# {c} {tag}: done {len([q for q in target if q in done])}/{len(target)}, "
              f"in flight {n_in}, skipped {len([q for q in target if q in skipped])}", file=sys.stderr)
    if not out and inflight_total == 0:
        print("ALLDONE")
    for o in out:
        print(o)


if __name__ == "__main__":
    main(sys.argv[1])
