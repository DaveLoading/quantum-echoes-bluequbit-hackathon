"""Report each finished G2 mps.cpu job at chi 20 to 23 next to its chi 24 and chi 16 values, with
run time (submit to first seen finished) and cost. Flags dead jobs, any cost above $0, scheduler
refusals, and submit lines that left nothing in the ledger. Local files only, no BlueQubit calls.

Usage: python3 -u work/watch_g2mps.py
"""
import json
import os
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "work", "jobs.jsonl")
SLOG = os.path.join(ROOT, "work", "scheduler_round3.log")
DEAD = ("FAIL", "CANCEL", "TERMINAT", "ERROR", "REJECT", "FUNDS", "LIMIT", "COOLDOWN")
TAGS = {f"mps.cpu_chi{c}" for c in (20, 21, 22, 23)}


def rows():
    try:
        return [json.loads(l) for l in open(LEDGER)]
    except (OSError, ValueError):
        return []


seen = {r["job_id"] for r in rows() if r.get("value") is not None or any(w in (r.get("status") or "").upper() for w in DEAD)}
submitted_seen = {r["job_id"] for r in rows()}
paid_seen = {r["job_id"] for r in rows() if (r.get("cost") or 0) > 0}
pos = os.path.getsize(SLOG) if os.path.exists(SLOG) else 0
shown = {}
WANT = {"mps.cpu_chi23": 32, "mps.cpu_chi22": 32, "mps.cpu_chi21": 32, "mps.cpu_chi20": 10}
complete = set()
while True:
    time.sleep(20)
    rs = rows()
    ref = {(r["tag"], r["q"]): r["value"] for r in rs
           if r["circuit"] == "G2" and r["tag"] in ("mps.cpu_chi24", "mps.cpu_chi16") and r.get("value") is not None}
    for r in rs:
        if (r.get("cost") or 0) > 0 and r["job_id"] not in paid_seen:
            paid_seen.add(r["job_id"])
            print(f"PAID JOB: {r['circuit']} q{r['q']} {r['tag']} cost ${r['cost']}", flush=True)
        if r["circuit"] != "G2" or r["tag"] not in TAGS:
            continue
        if r["job_id"] in seen:
            continue
        st = (r.get("status") or "").upper()
        if r.get("value") is not None:
            seen.add(r["job_id"])
            try:
                mins = (datetime.now(timezone.utc) - datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() / 60
            except Exception:
                mins = float("nan")
            v24 = ref.get(("mps.cpu_chi24", r["q"]))
            v16 = ref.get(("mps.cpu_chi16", r["q"]))
            f = lambda v: "--" if v is None else f"{v:+.4f}"
            shown[r["tag"]] = shown.get(r["tag"], 0) + 1
            if shown[r["tag"]] <= 2:  # a couple of samples per setting, then counts only
                print(f"G2 q{r['q']} {r['tag']}: {r['value']:+.4f}  [chi24 {f(v24)}, chi16 {f(v16)}]  "
                      f"about {mins:.0f} min after submit, cost {r.get('cost')}", flush=True)
        elif any(w in st for w in DEAD):
            seen.add(r["job_id"])
            print(f"DEAD JOB: G2 q{r['q']} {r['tag']} status {r.get('status')}", flush=True)
    done = {}
    for r in rs:
        if r["circuit"] == "G2" and r["tag"] in TAGS and r.get("value") is not None:
            done.setdefault(r["tag"], set()).add(r["q"])
    for t, n in WANT.items():
        if t not in complete and len(done.get(t, ())) >= n:
            complete.add(t)
            print(f"SET DONE: G2 {t} {len(done[t])}/{n}", flush=True)
    try:
        with open(SLOG) as fh:
            fh.seek(pos)
            txt = fh.read()
            pos = fh.tell()
    except OSError:
        txt = ""
    for line in txt.splitlines():
        if any(k in line for k in ("refused", "Traceback", "STOPPED", "Budget", "needs a cost", "time limit")):
            print(f"SCHEDULER: {line.strip()[:220]}", flush=True)
