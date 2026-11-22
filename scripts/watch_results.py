"""Print one line per newly finished job whose setting matches a filter (local ledger only).

Usage: python3 work/watch_results.py [substring ...]   (default: level0)
Polls work/jobs.jsonl every 30 s; prints jobs that finished (COMPLETED with a value, or a dead status)
since the watch started. No BlueQubit calls.
"""
import json
import os
import sys
import time

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.jsonl")
pats = sys.argv[1:] or ["level0"]
DEAD = ("FAIL", "CANCEL", "TERMINAT", "ERROR", "REJECT", "FUNDS", "LIMIT", "COOLDOWN")


def finished():
    out = {}
    try:
        for line in open(LEDGER):
            r = json.loads(line)
            st = (r.get("status") or "").upper()
            if any(p in r["tag"] for p in pats) and (r.get("value") is not None or any(w in st for w in DEAD)):
                out[r["job_id"]] = r
    except (OSError, ValueError):
        pass
    return out


seen = set(finished())
while True:
    time.sleep(30)
    now = finished()
    for jid, r in now.items():
        if jid not in seen:
            seen.add(jid)
            v = r.get("value")
            print(f"{r['circuit']} q{r['q']} {r['tag']}: {r['status']}" + (f" value {v:+.4f}" if v is not None else ""), flush=True)
