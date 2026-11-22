"""Print the kit's setting tag for a task line (helper for scheduler.sh). Local only."""
import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit"))
import bq_kit  # noqa: E402

a = shlex.split(sys.argv[1])
print(bq_kit.tag_for(a[a.index("--device") + 1], bq_kit.parse_opts([a[i + 1] for i, x in enumerate(a) if x == "--opt"])))
