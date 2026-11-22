"""Report each finished G4 level 3 job at a low threshold (below 7e-5) next to its 7e-5 and 1e-4
values, with run time (submit to first seen finished) and cost. Also flags dead jobs, any
cost above $0, and refusals in the scheduler log. Local files only, no BlueQubit calls.

Usage: python3 -u work/watch_lowth.py
"""
import json
import os
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "work", "jobs.jsonl")
SLOG = os.path.join(ROOT, "work", "scheduler_round3.log")
DEAD = ("FAIL", "CANCEL", "TERMINAT", "ERROR", "REJECT", "FUNDS", "LIMIT", "COOLDOWN")


def rows():
    try:
        return [json.loads(l) for l in open(LEDGER)]
    except (OSError, ValueError):
        return []


def th_of(tag):
    try:
        return float(tag.split("_th")[1])
    except (IndexError, ValueError):
        return None


def low(r):
    t = th_of(r["tag"])
    return r["circuit"] == "G4" and r["tag"].startswith("pauli-path_level3_th") and t is not None and t < 7e-5


seen = {r["job_id"] for r in rows() if r.get("value") is not None or any(w in (r.get("status") or "").upper() for w in DEAD)}
paid_seen = {r["job_id"] for r in rows() if (r.get("cost") or 0) > 0}
pos = os.path.getsize(SLOG) if os.path.exists(SLOG) else 0
while True:
    time.sleep(20)
    rs = rows()
    ref = {}
    for r in rs:
        if r["circuit"] == "G4" and r["tag"] in ("pauli-path_level3_th7e-05", "pauli-path_level3_th0.0001") and r.get("value") is not None:
            ref[(r["tag"], r["q"])] = r["value"]
    for r in rs:
        if (r.get("cost") or 0) > 0 and r["job_id"] not in paid_seen:
            paid_seen.add(r["job_id"])
            print(f"PAID JOB: {r['circuit']} q{r['q']} {r['tag']} cost ${r['cost']}", flush=True)
        if not low(r) or r["job_id"] in seen:
            continue
        st = (r.get("status") or "").upper()
        if r.get("value") is not None:
            seen.add(r["job_id"])
            try:
                mins = (datetime.now(timezone.utc) - datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() / 60
            except Exception:
                mins = float("nan")
            v7 = ref.get(("pauli-path_level3_th7e-05", r["q"]))
            v1 = ref.get(("pauli-path_level3_th0.0001", r["q"]))
            print(f"G4 q{r['q']} th {th_of(r['tag']):g}: {r['value']:+.4f}  [7e-5 {v7:+.4f}, 1e-4 {v1:+.4f}]  "
                  f"about {mins:.0f} min after submit, cost {r.get('cost')}", flush=True)
        elif any(w in st for w in DEAD):
            seen.add(r["job_id"])
            print(f"DEAD JOB: G4 q{r['q']} {r['tag']} status {r.get('status')}", flush=True)
    try:
        with open(SLOG) as f:
            f.seek(pos)
            txt = f.read()
            pos = f.tell()
    except OSError:
        txt = ""
    for line in txt.splitlines():
        if any(k in line for k in ("refused", "Traceback", "STOPPED", "Budget", "needs a cost", "time limit")):
            print(f"SCHEDULER: {line.strip()[:220]}", flush=True)
