"""Watch a list of setting tags for one circuit (local files only, no BlueQubit calls).

Usage: python3 -u work/watch_sets.py G4 tag1:36 tag2:36 tag3:12 ...
   or: python3 -u work/watch_sets.py G4 "label|tag|q1,q2,..." ...   (a named qubit subset of a tag)
Prints one line when:
  - a tag's set completes (n of N qubits have values)
  - any job for those tags ends in a dead status (refused, failed, cancelled, funds, limit)
  - any ledger job has a cost above $0
  - the scheduler log shows a refusal, a pricing failure (empty submit line or "needs a cost"),
    a Traceback, a budget stop, or a CSV write
  - every 12 newly finished jobs (a short progress line)
"""
import json
import os
import re
import sys
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "work", "jobs.jsonl")
SLOG = os.path.join(ROOT, "work", "scheduler_round3.log")
DEAD = ("FAIL", "CANCEL", "TERMINAT", "ERROR", "REJECT", "FUNDS", "LIMIT", "COOLDOWN")

circ = sys.argv[1]
want, tagof, qsets = {}, {}, {}
for a in sys.argv[2:]:
    if "|" in a:
        label, t, qs = a.split("|")
        qsets[label] = {int(q) for q in qs.split(",")}
    else:
        t, n = a.rsplit(":", 1)
        label = t
        qsets[label] = set(range(int(n)))
    tagof[label] = t
    want[label] = len(qsets[label])


def rows():
    try:
        return [json.loads(l) for l in open(LEDGER)]
    except (OSError, ValueError):
        return []


def state():
    done = {l: set() for l in want}
    dead, paid = {}, {}
    tags = set(tagof.values())
    for r in rows():
        if (r.get("cost") or 0) > 0:
            paid[r["job_id"]] = r
        if r["circuit"] != circ or r["tag"] not in tags:
            continue
        if r.get("value") is not None:
            for l, t in tagof.items():
                if t == r["tag"] and r["q"] in qsets[l]:
                    done[l].add(r["q"])
        elif any(w in (r.get("status") or "").upper() for w in DEAD):
            dead[r["job_id"]] = r
    return done, dead, paid


done0, dead_seen, paid_seen = state()
dead_seen, paid_seen = set(dead_seen), set(paid_seen)
complete = {t for t in want if len(done0[t]) >= want[t]}
finished_count = sum(len(v) for v in done0.values())
last_report = finished_count
pos = os.path.getsize(SLOG) if os.path.exists(SLOG) else 0
price_fail = re.compile(r"\] from [0-9,]+: *$")
pending = []

while True:
    time.sleep(30)
    done, dead, paid = state()
    for jid, r in paid.items():
        if jid not in paid_seen:
            paid_seen.add(jid)
            print(f"PAID JOB: {r['circuit']} q{r['q']} {r['tag']} cost ${r['cost']}", flush=True)
    for jid, r in dead.items():
        if jid not in dead_seen:
            dead_seen.add(jid)
            print(f"DEAD JOB: {r['circuit']} q{r['q']} {r['tag']} status {r.get('status')}", flush=True)
    for t in want:
        if t not in complete and len(done[t]) >= want[t]:
            complete.add(t)
            print(f"SET DONE: {circ} {t} ({tagof[t]}) {len(done[t])}/{want[t]}", flush=True)
    n = sum(len(v) for v in done.values())
    if n - last_report >= 12:
        last_report = n
        print("progress: " + ", ".join(f"{t} {len(done[t])}/{want[t]}" for t in want), flush=True)
    try:
        with open(SLOG) as f:
            f.seek(pos)
            txt = f.read()
            pos = f.tell()
    except OSError:
        txt = ""
    # An empty submit line is normal when the kit's output has no "submitted" line, so check the
    # ledger 60 s later: only flag it if none of those qubits got a row at that setting.
    for item in list(pending):
        t0, tag, qs = item
        if time.time() - t0 >= 60:
            pending.remove(item)
            have = {r["q"] for r in rows() if r["circuit"] == circ and r["tag"] == tag}
            if not (set(qs) & have):
                print(f"PRICE FAIL: {tag} q{','.join(map(str, qs))}: nothing reached the ledger (no price or refused before submit)", flush=True)
    for line in txt.splitlines():
        m = price_fail.search(line)
        if m:
            try:
                args = line.split("[", 1)[1].split("]", 1)[0]
                tag = subprocess.run([sys.executable, os.path.join(ROOT, "work", "sched_tag.py"), args],
                                     capture_output=True, text=True).stdout.strip()
                qs = [int(q) for q in line.rsplit("from ", 1)[1].rstrip(": ").split(",")]
                pending.append((time.time(), tag, qs))
            except Exception:
                print(f"SCHEDULER: {line.strip()[:220]}", flush=True)
            continue
        if ("needs a cost" in line or "refused" in line or "Traceback" in line
                or "STOPPED" in line or "Budget" in line or "wrote" in line or "time limit" in line):
            print(f"SCHEDULER: {line.strip()[:220]}", flush=True)
