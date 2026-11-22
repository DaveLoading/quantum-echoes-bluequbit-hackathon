"""Print how many ledger jobs are still pending or running (local file only, no BlueQubit calls)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit"))
import bq_kit  # noqa: E402

print(sum(1 for r in bq_kit.ledger_rows() if r.get("value") is None and not bq_kit._dead(r.get("status"))))
