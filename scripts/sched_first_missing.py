"""Print the first qubit in a list that has no ledger row at a setting (the one BlueQubit refused). Local only."""
import json
import os
import sys

c, tag, qs = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")]
ledger = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.jsonl")
have = set()
for line in open(ledger):
    r = json.loads(line)
    if r["circuit"] == c and r["tag"] == tag:
        have.add(r["q"])
print(next((q for q in qs if q not in have), ""))
