#!/usr/bin/env python3
"""Local control panel for the BlueQubit hackathon kit.

  python kit/dashboard.py [--rehearsal] [--port 8765] [--no-open]

Serves one page on 127.0.0.1 only. Every action runs
`python kit/bq_kit.py ...` as a child process (argv list, never a shell) from the
kit root, so the kit keeps doing the budget ledger, throttling, file names and
qubit order. The page never uploads or submits anything to the website: David
uploads every CSV himself.

Rehearsal mode (--rehearsal or BQ_MODE=rehearsal): every child gets
BQ_MODE=rehearsal and an empty BLUEQUBIT_API_TOKEN, so if the mode flag is ever
lost the kit exits instead of reaching the live account. Live mode: children
inherit the environment unchanged so the SDK can read the token itself.
This file never reads, prints, logs or returns the token or any environment dump.

The only file this server writes is work/submissions.md, and only by adding one
row to its uploads table through the "log an upload" form.
"""
import argparse
import hmac
import html
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(KIT_DIR)
sys.path.insert(0, KIT_DIR)
import bq_kit  # noqa: E402  (safe: defines functions and paths, calls nothing on import)

WORK = bq_kit.WORK
CSV_DIR = bq_kit.CSV_DIR
LEDGER = bq_kit.LEDGER
CIRCUITS = bq_kit.CIRCUITS
SUBMISSIONS = os.path.join(WORK, "submissions.md")
PAGE = os.path.join(KIT_DIR, "dashboard.html")
DOCS = {"circuits": "circuits.md", "tuning": "tuning.md", "picks": "picks.md", "submissions": "submissions.md"}

DEVICES = ("cpu", "gpu", "mps.cpu", "mps.gpu", "pauli-path")
OPT_KEYS = ("chi", "th", "level", "wcut")
NAME_RE = re.compile(r"[A-Za-z0-9]+")
QUBITS_RE = re.compile(r"[0-9,-]+")
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}")
CSV_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.csv")
INT_RE = re.compile(r"[0-9]{1,7}")
FLOAT_RE = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]{1,3})?")
SIGNED_RE = re.compile(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]{1,3})?")
EST_RE = re.compile(r"about \$([0-9.]+) and .* per qubit; x(\d+) qubits = about \$([0-9.]+)")

MAX_FREE_RUNNING = 4          # free commands the UI runs at once
CONFIRM_TTL = 600             # seconds a cost confirmation stays valid
MAX_UPLOADS = 10

REHEARSAL = False
PORT = 8765
SECRET = secrets.token_urlsafe(32)   # per launch; embedded in the page, required on every /api call
ALLOWED_HOSTS = set()
ALLOWED_ORIGINS = set()

LOCK = threading.Lock()       # command registry and console buffer
JOB_LOCK = threading.Lock()   # serialises the check-then-launch of run and collect
SUB_LOCK = threading.Lock()   # submissions.md writes
COMMANDS = {}                 # id -> Cmd
CONSOLE = deque(maxlen=20000)  # (seq, cmd_id, stream, text)
PENDING = {}                  # nonce -> prepared paid run (one at a time)
_seq = [0]
_next_id = [0]
_last_cfg = [None]
_last_db = [{}]


class Bad(Exception):
    """Invalid input: HTTP 400."""


class Refused(Exception):
    """Valid input, but a safety rule says no: HTTP 409."""

    def __init__(self, msg, extra=None):
        super().__init__(msg)
        self.extra = extra or {}


# ------------------------------------------------------------------ reading kit files
def kit_cfg():
    for _ in range(3):
        try:
            c = bq_kit.cfg()
            _last_cfg[0] = c
            return c
        except (OSError, ValueError):
            time.sleep(0.1)
    if _last_cfg[0] is None:
        raise Refused("config.json can't be read right now.")
    return _last_cfg[0]


def cfg_numbers(c):
    def f(key, default):
        try:
            return float(c.get(key, default))
        except (TypeError, ValueError):
            return float(default)
    return {
        "prefix": str(c.get("prefix", "?")),
        "budget": f("budget_usd", 0.0),
        "ask": f("ask_david_above_usd", 2.0),
        "default_cap": f("default_max_cost_per_run", 2.0),
        "max_parallel": int(f("max_parallel", 8)),
    }


def circuits_db():
    for _ in range(3):
        try:
            if not os.path.exists(CIRCUITS):
                return {}
            with open(CIRCUITS, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _last_db[0] = d
                return d
        except (OSError, ValueError):
            time.sleep(0.1)
    return _last_db[0]


def read_ledger():
    """Rows of work/jobs.jsonl. Retries when a line doesn't parse (the kit may be mid-rewrite)."""
    rows, bad = [], 0
    for _ in range(4):
        rows, bad = [], 0
        try:
            with open(LEDGER, encoding="utf-8") as f:
                data = f.read()
        except FileNotFoundError:
            return [], True
        except OSError:
            time.sleep(0.1)
            continue
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
        if not bad:
            return rows, True
        time.sleep(0.15)
    return rows, False


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def spent_of(rows):
    return round(sum(num(r.get("cost")) for r in rows), 4)


def _dead(status):
    """Same rule as bq_kit._dead: set, and not PENDING/QUEUED/RUNNING/COMPLETED."""
    s = str(status or "").upper()
    return bool(s) and s not in ("PENDING", "QUEUED", "RUNNING", "COMPLETED")


def ids_of(name, nq):
    """IDs (jobs) for a circuit: config.json "ids" override, else one per qubit. Same rule as bq_kit.id_count."""
    try:
        o = (kit_cfg().get("ids") or {}).get(name)
        if o is not None:
            return int(o)
    except (Refused, AttributeError, TypeError, ValueError):
        pass
    return nq


def committed_of(rows):
    """Spent plus jobs still waiting for `collect`, priced at the real per-qubit cost of
    their setting where known. Matches how bq_kit's own budget check counts them."""
    known = {}
    for r in rows:
        if r.get("status") == "COMPLETED" and r.get("cost") is not None:
            known.setdefault((r.get("circuit"), r.get("tag")), []).append(num(r.get("cost")))
    waiting = 0.0
    for r in rows:
        k = known.get((r.get("circuit"), r.get("tag")))
        if r.get("cost") is None and not _dead(r.get("status")) and k:
            waiting += sum(k) / len(k)
    return round(spent_of(rows) + waiting, 4)


def is_pending(r):
    # a --no-wait job can already say COMPLETED with no value until `collect` fetches it
    return not _dead(r.get("status")) and r.get("value") is None


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def list_csvs(circuit=None):
    try:
        names = os.listdir(CSV_DIR)
    except OSError:
        return []
    out = []
    for b in sorted(names, key=natural):
        if not b.endswith(".csv") or "__" not in b:
            continue
        c, tag = b[:-4].split("__", 1)
        if circuit is not None and c != circuit:
            continue
        p = os.path.join(CSV_DIR, b)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        out.append({"file": "work/csv/" + b, "abs": p, "base": b, "circuit": c, "tag": tag,
                    "mtime": st.st_mtime, "size": st.st_size, "pickable": bool(CSV_NAME_RE.fullmatch(b))})
    return out


# ------------------------------------------------------------------ submissions.md
def split_cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


SEP_RE = re.compile(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*")


def find_uploads_table(lines):
    """Locate the uploads table: header has a Circuit column and a Score column."""
    for i, line in enumerate(lines):
        if not line.strip().startswith("|") or i + 1 >= len(lines):
            continue
        head = [c.lower() for c in split_cells(line)]
        if "circuit" in head and any(c.startswith("score") for c in head) and SEP_RE.fullmatch(lines[i + 1].rstrip("\r\n")):
            last = i + 1
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                last = j
                j += 1
            return {"header_idx": i, "head": head, "first_row": i + 2, "last_idx": last}
    return None


def col(head, *starts):
    for k, h in enumerate(head):
        if any(h == s or h.startswith(s) for s in starts):
            return k
    return None


def first_float(s):
    m = SIGNED_RE.search(s or "")
    if not m:
        return None
    try:
        v = float(m.group(0))
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def read_submissions():
    try:
        with open(SUBMISSIONS, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    lines = text.splitlines()
    t = find_uploads_table(lines)
    if not t:
        return []
    head = t["head"]
    ci, fi, si, ui, ti, ni = (col(head, "circuit"), col(head, "file"), col(head, "score"),
                              col(head, "upload"), col(head, "time"), col(head, "note"))
    out = []
    for line in lines[t["first_row"]:t["last_idx"] + 1]:
        cells = split_cells(line)
        get = lambda k: cells[k].strip().strip("`").strip() if k is not None and k < len(cells) else ""
        out.append({"circuit": get(ci), "file": get(fi), "score_text": get(si), "score": first_float(get(si)),
                    "upload": get(ui), "time": get(ti), "note": get(ni)})
    return out


def uploads_by_circuit(subs):
    d = {}
    for s in subs:
        e = d.setdefault(s["circuit"], {"used": 0, "best": None, "files": []})
        e["used"] += 1
        if s["score"] is not None and (e["best"] is None or s["score"] > e["best"]):
            e["best"] = s["score"]
        if s["file"]:
            e["files"].append(os.path.basename(s["file"]))
    return d


def phoenix_now():
    try:
        from zoneinfo import ZoneInfo
        t = datetime.now(ZoneInfo("America/Phoenix"))
    except Exception:
        from datetime import timedelta
        t = datetime.now(timezone(timedelta(hours=-7)))
    return t.strftime("%Y-%m-%d %H:%M")


def cell_text(s, limit):
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s))
    s = re.sub(r"\s+", " ", s).strip()[:limit]
    return s.replace("|", "\\|")


def log_upload(body):
    c = v_circuit(body.get("circuit"))
    rel = v_csv_file(body.get("file"))
    base = os.path.basename(rel)
    if not base.startswith(c + "__"):
        raise Bad(f"{base} is not a {c} file.")
    score = str(body.get("score") if body.get("score") is not None else "").strip()
    if not SIGNED_RE.fullmatch(score) or not math.isfinite(float(score)):
        raise Bad("Score shown must be a number, as the site displays it.")
    note = cell_text(body.get("note") or "", 300)
    with SUB_LOCK:
        try:
            with open(SUBMISSIONS, encoding="utf-8", newline="") as f:
                text = f.read()
        except OSError:
            raise Refused("work/submissions.md doesn't exist. Create it with its uploads table first.")
        lines = text.splitlines(keepends=True)
        t = find_uploads_table([ln.rstrip("\r\n") for ln in lines])
        if not t:
            raise Refused("work/submissions.md has no uploads table (a header with Circuit and Score columns).")
        used = sum(1 for s in read_submissions() if s["circuit"] == c)
        if used >= MAX_UPLOADS:
            raise Refused(f"{c} already has {used} of {MAX_UPLOADS} uploads logged. The site allows no more.")
        n = used + 1
        values = []
        for h in t["head"]:
            if h.startswith("time"):
                values.append(phoenix_now() + (" rehearsal" if REHEARSAL else ""))
            elif h == "circuit":
                values.append(c)
            elif h.startswith("file"):
                values.append(rel)
            elif h.startswith("score"):
                values.append(score)
            elif h.startswith("upload"):
                values.append(f"{n} of {MAX_UPLOADS}")
            elif h.startswith("note"):
                values.append(note)
            else:
                values.append("")
        row = "| " + " | ".join(values) + " |"
        nl = "\r\n" if lines and lines[t["last_idx"]].endswith("\r\n") else "\n"
        tail = "".join(lines[t["last_idx"] + 1:])
        if tail == "":
            # table is the end of the file: a pure append
            with open(SUBMISSIONS, "a", encoding="utf-8", newline="") as f:
                f.write(("" if text.endswith("\n") or not text else nl) + row + nl)
        else:
            # something follows the table: insert the row right after its last row
            new = "".join(lines[:t["last_idx"] + 1])
            if not new.endswith("\n"):
                new += nl
            new += row + nl + tail
            with open(SUBMISSIONS, "r+", encoding="utf-8", newline="") as f:
                f.seek(0)
                f.write(new)
                f.truncate()
    return {"ok": True, "upload": n, "row": row}


# ------------------------------------------------------------------ markdown (escape first, then format)
def esc(s):
    return html.escape(s, quote=True)


def md_inline(s):
    out = []
    for part in re.split(r"(`[^`]*`)", s):
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            out.append("<code>" + esc(part[1:-1]) + "</code>")
            continue
        t = esc(part)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                   lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>', t)
        t = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", t)
        out.append(t)
    return "".join(out)


LI_RE = re.compile(r"(\s*)([-*+]|\d+[.)])\s+(.*)")
HR_RE = re.compile(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*")
H_RE = re.compile(r"\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*")


def md_table(rows_raw, sep):
    aligns = []
    for c in split_cells(sep):
        c = c.strip()
        aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "")
    head = split_cells(rows_raw[0])
    a = lambda k: f' class="al-{aligns[k]}"' if k < len(aligns) and aligns[k] else ""
    out = ['<div class="tablewrap"><table class="md"><thead><tr>']
    out += [f"<th{a(k)}>{md_inline(c)}</th>" for k, c in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for r in rows_raw[1:]:
        cells = split_cells(r)
        out.append("<tr>" + "".join(f"<td{a(k)}>{md_inline(c)}</td>" for k, c in enumerate(cells)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def md_list(items):
    out, stack = [], []
    for indent, ordered, text in items:
        while stack and indent < stack[-1][0]:
            out.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0]:
            out.append("</li>")
        elif not stack or indent > stack[-1][0]:
            tag = "ol" if ordered else "ul"
            stack.append((indent, tag))
            out.append(f"<{tag}>")
        out.append("<li>" + md_inline(text))
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def md_to_html(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out, i = [], 0

    def starts_block(k):
        ln = lines[k]
        return (ln.strip().startswith("```") or H_RE.fullmatch(ln) or LI_RE.fullmatch(ln) or ln.lstrip().startswith(">")
                or HR_RE.fullmatch(ln) or (ln.strip().startswith("|") and k + 1 < len(lines) and SEP_RE.fullmatch(lines[k + 1])))

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.strip().startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
        elif H_RE.fullmatch(ln):
            m = H_RE.fullmatch(ln)
            lvl = min(6, len(m.group(1)) + 1)
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            i += 1
        elif ln.strip().startswith("|") and i + 1 < len(lines) and SEP_RE.fullmatch(lines[i + 1]):
            rows, sep = [ln], lines[i + 1]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(md_table(rows, sep))
        elif HR_RE.fullmatch(ln):
            out.append("<hr>")
            i += 1
        elif LI_RE.fullmatch(ln):
            items = []
            while i < len(lines):
                m = LI_RE.fullmatch(lines[i])
                if m:
                    items.append([len(m.group(1).expandtabs(4)), m.group(2)[0].isdigit(), m.group(3)])
                    i += 1
                elif lines[i].strip() and lines[i][:1] in (" ", "\t") and items:
                    items[-1][2] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(md_list(items))
        elif ln.lstrip().startswith(">"):
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + md_to_html("\n".join(quote)) + "</blockquote>")
        else:
            para = [ln.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not starts_block(i):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>" + md_inline(" ".join(para)) + "</p>")
    return "\n".join(out)


# ------------------------------------------------------------------ job slot (rule 5)
def scan_job_processes():
    """(pid, 'run'|'collect') for every python process running bq_kit.py run/collect.
    Returns None if ps can't be read. The ps text never leaves this function."""
    try:
        res = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                             timeout=5, shell=False, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    found = []
    for line in res.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        argv = parts[1].split()
        if not argv or "python" not in os.path.basename(argv[0]).lower():
            continue
        for k in range(1, len(argv) - 1):
            if os.path.basename(argv[k]) == "bq_kit.py" and argv[k + 1] in ("run", "collect"):
                found.append((int(parts[0]), argv[k + 1]))
                break
    return found


def job_slot():
    procs = scan_job_processes()
    with LOCK:
        mine = {c.pid for c in COMMANDS.values() if c.running and c.pid}
        ui = [c for c in COMMANDS.values() if c.running and c.job]
    ui_job = ui[0].label if ui else None
    if procs is None:
        return {"known": False, "busy": True, "external": 0, "ui_job": ui_job, "kinds": []}
    external = [p for p in procs if p[0] not in mine]
    return {"known": True, "busy": bool(external) or bool(ui), "external": len(external),
            "ui_job": ui_job, "kinds": sorted({k for _, k in procs})}


def slot_message(slot):
    if not slot["known"]:
        return "Couldn't check running processes, so run and collect are blocked. Try again in a moment."
    why = []
    if slot["ui_job"]:
        why.append(f"this dashboard is running `{slot['ui_job']}`")
    if slot["external"]:
        why.append(f"{slot['external']} other bq_kit run/collect process(es) are active, likely an agent")
    return "Job slot busy: " + "; ".join(why) + ". One job-submitting command at a time (rule 5)."


# ------------------------------------------------------------------ running kit commands
class Cmd:
    def __init__(self, cid, argv, job, internal=False):
        self.id = cid
        self.argv = argv
        self.label = "bq_kit.py " + " ".join(argv)
        self.job = job
        self.internal = internal
        self.started = time.time()
        self.ended = None
        self.code = None
        self.pid = None
        self.proc = None
        self.running = True
        self.out = []          # stdout lines, kept for parsing (estimate)

    def summary(self):
        return {"id": self.id, "label": self.label, "job": self.job, "running": self.running,
                "code": self.code, "started": self.started, "ended": self.ended}


def add_line(cid, stream, text):
    with LOCK:
        _seq[0] += 1
        CONSOLE.append((_seq[0], cid, stream, text[:4000]))


def child_env():
    if REHEARSAL:
        env = {k: v for k, v in os.environ.items() if k != "BLUEQUBIT_API_TOKEN"}
        env["BQ_MODE"] = "rehearsal"
        env["BLUEQUBIT_API_TOKEN"] = ""
        return env
    return None  # live: inherit unchanged


def _pump(cmd, stream, name):
    try:
        for line in stream:
            line = line.rstrip("\n")
            if name == "out":
                cmd.out.append(line)
            add_line(cmd.id, name, line)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _waiter(cmd, pumps):
    code = cmd.proc.wait()
    for t in pumps:
        t.join(timeout=5)
    cmd.code = code
    cmd.ended = time.time()
    cmd.running = False
    add_line(cmd.id, "meta", f"exit code {code}  ({cmd.ended - cmd.started:.1f} s)")


def launch(argv, job=False, internal=False):
    with LOCK:
        if not job and not internal:
            busy = sum(1 for c in COMMANDS.values() if c.running and not c.job and not c.internal)
            if busy >= MAX_FREE_RUNNING:
                raise Refused(f"{busy} commands are already running. Wait for one to finish.")
        _next_id[0] += 1
        cmd = Cmd(_next_id[0], argv, job, internal)
        COMMANDS[cmd.id] = cmd
        done = [k for k, c in COMMANDS.items() if not c.running]
        for k in done[:max(0, len(COMMANDS) - 100)]:
            del COMMANDS[k]
    add_line(cmd.id, "meta", f"$ python kit/bq_kit.py {' '.join(argv)}   [{'rehearsal' if REHEARSAL else 'LIVE'}]")
    try:
        proc = subprocess.Popen([sys.executable, "-u", "kit/bq_kit.py", *argv], cwd=ROOT, env=child_env(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1,
                                shell=False, close_fds=True, start_new_session=True)
    except OSError as ex:
        cmd.running, cmd.code, cmd.ended = False, -1, time.time()
        add_line(cmd.id, "meta", f"could not start ({type(ex).__name__})")
        raise Refused("Could not start the kit command.")
    cmd.proc, cmd.pid = proc, proc.pid
    pumps = [threading.Thread(target=_pump, args=(cmd, proc.stdout, "out"), daemon=True),
             threading.Thread(target=_pump, args=(cmd, proc.stderr, "err"), daemon=True)]
    for t in pumps:
        t.start()
    threading.Thread(target=_waiter, args=(cmd, pumps), daemon=True).start()
    return cmd


# ------------------------------------------------------------------ input validation
def v_circuit(x):
    if not isinstance(x, str) or len(x) > 40 or not NAME_RE.fullmatch(x):
        raise Bad("Circuit name must be letters and digits only (like G1).")
    return x


def v_device(x):
    if x not in DEVICES:
        raise Bad("Device must be one of: " + ", ".join(DEVICES) + ".")
    return x


def v_int(x, lo, hi, what):
    s = str(x).strip() if isinstance(x, (str, int)) and not isinstance(x, bool) else ""
    if not INT_RE.fullmatch(s) or not lo <= int(s) <= hi:
        raise Bad(f"{what} must be a whole number from {lo} to {hi}.")
    return int(s)


def v_float(x, what, lo=None, hi=None, lo_open=False):
    s = str(x).strip() if isinstance(x, (str, int, float)) and not isinstance(x, bool) else ""
    if not FLOAT_RE.fullmatch(s):
        raise Bad(f"{what} must be a plain positive number (like 0.5 or 1e-3).")
    v = float(s)
    if not math.isfinite(v) or (lo is not None and (v <= lo if lo_open else v < lo)) or (hi is not None and v > hi):
        raise Bad(f"{what} is out of range.")
    return s, v


def v_opts(items):
    if not isinstance(items, list) or len(items) > 4:
        raise Bad("Options must be a short list.")
    out, seen = [], set()
    for it in items:
        if isinstance(it, dict):
            k, v = it.get("key"), it.get("value")
        elif isinstance(it, str) and "=" in it:
            k, v = it.split("=", 1)
        else:
            raise Bad("Each option is key=value.")
        if k not in OPT_KEYS:
            raise Bad("Option keys allowed: " + ", ".join(OPT_KEYS) + ".")
        if k in seen:
            raise Bad(f"Option {k} given twice.")
        seen.add(k)
        if k == "chi":
            s = str(v_int(v, 1, 65536, "chi"))
        elif k == "level":
            s = str(v_int(v, 0, 3, "level"))
        elif k == "th":
            s, _ = v_float(v, "th", lo=0.0, hi=1.0, lo_open=True)
        else:
            s, _ = v_float(v, "wcut", lo=0.0, hi=1e6)
        out.append(f"{k}={s}")
    return out


def v_qubits(spec):
    spec = (spec or "").strip() if isinstance(spec, str) else None
    if spec is None:
        raise Bad("Qubits must be text like 0-2,7.")
    if not spec:
        return "", None
    if len(spec) > 200 or not QUBITS_RE.fullmatch(spec):
        raise Bad("Qubits may only contain digits, commas and dashes (like 0-2,7).")
    qs = []
    for part in spec.split(","):
        m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part)
        if not m:
            raise Bad(f"Qubit part '{part}' isn't a number or a range like 0-2.")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) is not None else lo
        if hi < lo:
            raise Bad(f"Range {part} runs backwards.")
        qs.extend(range(lo, hi + 1))
    if len(qs) != len(set(qs)):
        raise Bad("A qubit appears twice; the kit would run (and pay for) it twice.")
    return spec, qs


def v_cost(x):
    _, v = v_float(x, "Max cost", lo=0.0, hi=10000.0, lo_open=True)
    return v


def v_csv_file(x):
    if not isinstance(x, str) or not x or len(x) > 500 or "\x00" in x:
        raise Bad("Pick a CSV file from work/csv/.")
    p = x if os.path.isabs(x) else os.path.join(ROOT, x)
    real = os.path.realpath(p)
    base = os.path.basename(real)
    if os.path.dirname(real) != os.path.realpath(CSV_DIR) or not CSV_NAME_RE.fullmatch(base):
        raise Bad("Files must be .csv files directly inside work/csv/.")
    if not os.path.isfile(real):
        raise Bad(f"work/csv/{base} doesn't exist.")
    return "work/csv/" + base


def v_tag(x):
    if not isinstance(x, str) or not TAG_RE.fullmatch(x):
        raise Bad("Tag must look like mps.cpu_chi64 or pauli-path_th0.001.")
    return x


def build_argv(cmd, a):
    """Validated kit argv for a free command (everything except run and collect)."""
    if cmd == "status":
        return ["status"]
    if cmd == "info":
        raw = a.get("circuits")
        names = raw.replace(",", " ").split() if isinstance(raw, str) else raw
        if not isinstance(names, list) or not 1 <= len(names) <= 40:
            raise Bad("Give one or more circuit names.")
        return ["info", *[v_circuit(n) for n in names]]
    if cmd == "points":
        return ["points", v_circuit(a.get("circuit")), str(v_int(a.get("points"), 0, 1000000, "Points"))]
    if cmd == "describe":
        show = a.get("show") if a.get("show") not in (None, "") else "2"
        return ["describe", v_circuit(a.get("circuit")), "--show", str(v_int(show, 1, 100, "Files to show"))]
    if cmd == "exact":
        return ["exact", v_circuit(a.get("circuit"))]
    if cmd == "local":
        c = v_circuit(a.get("circuit"))
        m = a.get("method")
        if m == "mps":
            chi = a.get("chi") if a.get("chi") not in (None, "") else "32"
            return ["local", c, "--method", "mps", "--chi", str(v_int(chi, 1, 4096, "chi"))]
        if m == "pp":
            th = a.get("th") if a.get("th") not in (None, "") else "0.01"
            s, _ = v_float(th, "th", lo=0.0, hi=1.0, lo_open=True)
            return ["local", c, "--method", "pp", "--th", s]
        raise Bad("Method must be mps or pp.")
    if cmd == "estimate":
        return ["estimate", v_circuit(a.get("circuit")), "--device", v_device(a.get("device"))]
    if cmd == "candidates":
        return ["candidates", v_circuit(a.get("circuit"))]
    if cmd == "validate":
        return ["validate", v_circuit(a.get("circuit")), v_csv_file(a.get("file"))]
    if cmd == "score":
        return ["score", v_csv_file(a.get("a")), v_csv_file(a.get("b"))]
    if cmd == "build":
        argv = ["build", v_circuit(a.get("circuit")), v_tag(a.get("tag"))]
        return argv + (["--fill-missing"] if a.get("fill_missing") is True else [])
    if cmd == "blend":
        c = v_circuit(a.get("circuit"))
        files = a.get("files")
        if not isinstance(files, list) or not 2 <= len(files) <= 8:
            raise Bad("Pick 2 to 8 CSV files to blend.")
        files = [v_csv_file(f) for f in files]
        if len(set(files)) != len(files):
            raise Bad("A file is picked twice.")
        for f in files:
            if not os.path.basename(f).startswith(c + "__"):
                raise Bad(f"{os.path.basename(f)} is not a {c} file.")
        argv = ["blend", c, *files]
        w = (a.get("weights") or "").strip() if isinstance(a.get("weights"), str) else ""
        if w:
            parts = w.split(",")
            if len(parts) != len(files):
                raise Bad("Give one weight per file, comma separated.")
            vals = [v_float(p.strip(), "Weight", lo=0.0)[1] for p in parts]
            if sum(vals) <= 0:
                raise Bad("Weights must not all be zero.")
            argv += ["--weights", ",".join(p.strip() for p in parts)]
        name = (a.get("name") or "").strip() if isinstance(a.get("name"), str) else ""
        if name:
            argv += ["--name", v_tag(name)]
        else:
            name = "blend_" + "+".join(os.path.basename(f).split("__", 1)[1].rsplit(".", 1)[0] for f in files)
        if os.path.exists(os.path.join(CSV_DIR, f"{c}__{name}.csv")):
            raise Refused(f"work/csv/{c}__{name}.csv already exists. Give the blend a new name so nothing is overwritten.")
        return argv
    if cmd == "rehearsal-score":
        if not REHEARSAL:
            raise Refused("rehearsal-score only works in rehearsal mode.")
        return ["rehearsal-score", v_circuit(a.get("circuit")), v_csv_file(a.get("file"))]
    raise Bad("Unknown command.")


# ------------------------------------------------------------------ paid run: estimate, confirm, launch
def run_estimate(circuit, device, opts=()):
    argv = ["estimate", circuit, "--device", device]
    for o in opts:
        argv += ["--opt", o]
    cmd = launch(argv, internal=True)
    deadline = time.time() + 120
    while cmd.running and time.time() < deadline:
        time.sleep(0.1)
    if cmd.running:
        try:
            cmd.proc.kill()
        except OSError:
            pass
        return None, None, None, "The estimate timed out."
    per = n = total = None
    note = None
    for line in cmd.out:
        m = EST_RE.search(line)
        if m:
            per, n, total = float(m.group(1)), int(m.group(2)), float(m.group(3))
        elif line.startswith(("estimate failed", "no estimate available", "warning:")):
            note = line
    if per is None and note is None:
        note = "No estimate came back (exit code %s)." % cmd.code
    return per, n, total, note


def evaluate(p, numbers, spent):
    budget, ask = numbers["budget"], numbers["ask"]
    est, cap = p["est_run"], p["cap"]
    worst = spent + max(est or 0.0, cap)
    reasons = []
    if est is None:
        reasons.append("No estimate is available, so the cost is unknown.")
    elif est > ask:
        reasons.append(f"The estimate (${est:.2f}) is above the ${ask:.2f} ask-David line.")
    if cap > ask:
        reasons.append(f"The cap (${cap:.2f}) is above the ${ask:.2f} ask-David line.")
    if budget > 0 and worst > 0.8 * budget:
        reasons.append(f"Spent plus this run (${worst:.2f}) would pass 80% of the budget (${0.8 * budget:.2f}).")
    if p["no_wait"]:
        reasons.append("--no-wait: the kit sends only the qubits that fit the cap, priced from real costs at this "
                       "setting, or from the estimate (which may ignore chi/th) if none have finished yet.")
    return {
        "need_typed": bool(reasons), "reasons": reasons, "spent": spent, "budget": budget, "ask": ask,
        "warn_line": 0.8 * budget,
        "pct_after_est": (spent + (est or 0.0)) / budget * 100 if budget > 0 else None,
        "pct_after_cap": worst / budget * 100 if budget > 0 else None,
    }


def public_pending(p, ev, numbers):
    keys = ("nonce", "circuit", "device", "opts", "qubits", "cap", "no_wait", "tag", "est_per", "est_full",
            "est_run", "n", "count", "done_count", "est_note", "argv")
    d = {k: p[k] for k in keys}
    d.update(ev)
    d["expires"] = p["created"] + CONFIRM_TTL
    d["max_parallel"] = numbers["max_parallel"]
    d["command"] = "python kit/bq_kit.py " + " ".join(p["argv"])
    return d


def prepare_run(body):
    c = v_circuit(body.get("circuit"))
    d = v_device(body.get("device"))
    opts = v_opts(body.get("opts") or [])
    spec, qlist = v_qubits(body.get("qubits") if body.get("qubits") is not None else "")
    numbers = cfg_numbers(kit_cfg())
    raw_cap = body.get("max_cost")
    cap = v_cost(raw_cap if raw_cap not in (None, "") else repr(numbers["default_cap"]))
    no_wait = body.get("no_wait") is True
    slot = job_slot()
    if slot["busy"]:
        raise Refused(slot_message(slot), {"slot": slot})
    rows, _ = read_ledger()
    spent = committed_of(rows)
    if numbers["budget"] <= 0 or spent >= numbers["budget"]:
        raise Refused(f"Budget used up (${spent:.2f} of ${numbers['budget']:.2f}). Ask David before spending more.")
    tag = bq_kit.tag_for(d, bq_kit.parse_opts(opts))
    per, n_est, total, note = run_estimate(c, d, opts)
    info = circuits_db().get(c)
    n = ids_of(c, info.get("num_qubits") if isinstance(info, dict) else None) or n_est   # jobs = IDs
    qubits = qlist if qlist is not None else (list(range(n)) if n else None)
    if qubits is not None and n:
        qubits = [q for q in qubits if q < n]
    done = {r.get("q") for r in rows if r.get("circuit") == c and r.get("tag") == tag
            and r.get("status") == "COMPLETED" and r.get("value") is not None}
    to_run = [q for q in qubits if q not in done] if qubits is not None else None
    count = len(to_run) if to_run is not None else None
    est_run = per * count if per is not None and count is not None else (total if qlist is None else None)
    argv = ["run", c, "--device", d]
    for o in opts:
        argv += ["--opt", o]
    if spec:
        argv += ["--qubits", spec]
    argv += ["--max-cost", repr(cap)]
    if no_wait:
        argv.append("--no-wait")
    p = {"nonce": secrets.token_urlsafe(18), "created": time.time(), "argv": argv, "circuit": c, "device": d,
         "opts": opts, "qubits": spec, "cap": cap, "no_wait": no_wait, "tag": tag, "est_per": per,
         "est_full": (per * n if per is not None and n else total), "est_run": est_run, "n": n, "count": count,
         "done_count": (len(qubits) - count) if count is not None else None, "est_note": note}
    ev = evaluate(p, numbers, spent)
    with JOB_LOCK:
        PENDING.clear()
        PENDING[p["nonce"]] = p
    return public_pending(p, ev, numbers)


def confirm_run(body):
    nonce, typed = body.get("nonce"), body.get("typed")
    if not isinstance(nonce, str) or not isinstance(typed, (str, type(None))):
        raise Bad("Bad confirmation.")
    with JOB_LOCK:
        p = PENDING.pop(nonce, None)
        if not p or time.time() - p["created"] > CONFIRM_TTL:
            raise Refused("This confirmation expired or was already used. Check the cost again.", {"stale": True})
        slot = job_slot()                       # first, so nobody is rewriting the ledger while we read it
        if slot["busy"]:
            raise Refused(slot_message(slot), {"slot": slot, "stale": True})
        numbers = cfg_numbers(kit_cfg())
        rows, clean_read = read_ledger()
        if not clean_read:
            PENDING[nonce] = p
            raise Refused("The ledger couldn't be read cleanly. Try again in a few seconds.")
        spent = committed_of(rows)
        if numbers["budget"] <= 0 or spent >= numbers["budget"]:
            raise Refused(f"Budget used up (${spent:.2f} of ${numbers['budget']:.2f}). Ask David before spending more.",
                          {"stale": True})
        ev = evaluate(p, numbers, spent)       # re-checked here: the page's view is not trusted
        if ev["need_typed"] and (typed or "").strip() != p["circuit"]:
            PENDING[nonce] = p
            raise Refused(f"Type the circuit name ({p['circuit']}) exactly to confirm.",
                          {"panel": public_pending(p, ev, numbers)})
        cmd = launch(p["argv"], job=True)
    return {"ok": True, "cmd": cmd.id}


def start_collect():
    with JOB_LOCK:
        slot = job_slot()
        if slot["busy"]:
            raise Refused(slot_message(slot), {"slot": slot})
        cmd = launch(["collect"], job=True)
    return {"ok": True, "cmd": cmd.id}


# ------------------------------------------------------------------ views
def state():
    numbers = cfg_numbers(kit_cfg())
    rows, ledger_ok = read_ledger()
    db = circuits_db()
    csvs = list_csvs()
    subs = read_submissions()
    ups = uploads_by_circuit(subs)
    names = set(db) | {f["circuit"] for f in csvs}
    circuits = []
    for name in sorted(names, key=natural):
        info = db.get(name) or {}
        ops = info.get("count_ops") or {}
        u = ups.get(name, {})
        circuits.append({
            "name": name, "qubits": info.get("num_qubits"), "ids": ids_of(name, info.get("num_qubits")),
            "depth_2q": info.get("depth_2q"), "order": ops.get("x") if isinstance(ops, dict) else None, "points": info.get("points"),
            "csvs": sum(1 for f in csvs if f["circuit"] == name), "uploads": u.get("used", 0), "best": u.get("best"),
        })
    tags = {}
    for r in rows:
        if isinstance(r.get("circuit"), str) and isinstance(r.get("tag"), str):
            tags.setdefault(r["circuit"], set()).add(r["tag"])
    with LOCK:
        cmds = [c.summary() for c in sorted(COMMANDS.values(), key=lambda c: -c.id)]
    return {
        "mode": "rehearsal" if REHEARSAL else "live", **numbers,
        "spent": spent_of(rows), "pending": sum(1 for r in rows if is_pending(r)), "jobs": len(rows),
        "ledger_ok": ledger_ok, "slot": job_slot(), "circuits": circuits,
        "csvs": [{k: f[k] for k in ("file", "abs", "base", "circuit", "tag", "mtime", "pickable")} for f in csvs],
        "tags": {k: sorted(v, key=natural) for k, v in tags.items()},
        "uploads": {k: {"used": v["used"], "best": v["best"]} for k, v in ups.items()},
        "docs": {k: os.path.exists(os.path.join(WORK, v)) for k, v in DOCS.items()},
        "commands": cmds[:60], "max_uploads": MAX_UPLOADS, "now": time.time(),
    }


def pearson(a, b):
    if len(a) != len(b) or len(a) < 2 or a.std() == 0 or b.std() == 0:
        return None
    return float(bq_kit.np.corrcoef(a, b)[0, 1])


def tag_spend(rows, circuit=None):
    out = {}
    for r in rows:
        if circuit is not None and r.get("circuit") != circuit:
            continue
        key = (str(r.get("circuit")), str(r.get("tag")))
        e = out.setdefault(key, {"circuit": key[0], "tag": key[1], "device": r.get("device"), "jobs": 0,
                                 "completed": 0, "failed": 0, "pending": 0, "cost": 0.0})
        e["jobs"] += 1
        e["cost"] += num(r.get("cost"))
        st = r.get("status")
        e["completed" if st == "COMPLETED" else "failed" if _dead(st) else "pending"] += 1
    return sorted(out.values(), key=lambda e: (natural(e["circuit"]), -e["cost"], natural(e["tag"])))


def circuit_detail(name):
    name = v_circuit(name)
    db = circuits_db()
    info = db.get(name) or {}
    subs = [s for s in read_submissions() if s["circuit"] == name]
    uploaded = {os.path.basename(s["file"]) for s in subs if s["file"]}
    cands, data = [], {}
    for f in list_csvs(name):
        e = {"tag": f["tag"], "file": f["file"], "abs": f["abs"], "base": f["base"], "mtime": f["mtime"],
             "pickable": f["pickable"], "uploaded": f["base"] in uploaded, "ok": False}
        try:
            v = bq_kit.read_csv(f["abs"])
            if len(v) == 0 or not bq_kit.np.all(bq_kit.np.isfinite(v)):
                raise ValueError("empty or non-finite values")
            data[f["tag"]] = v
            e.update(ok=True, n=len(v), values=[float(x) for x in v], z=[float(x) for x in bq_kit.zscore(v)])
        except Exception as ex:  # a CSV being written, or malformed
            e["error"] = type(ex).__name__
        cands.append(e)
    names = [e["tag"] for e in cands if e["ok"]]
    truth = data.get("exact_local")
    up_names = [e["tag"] for e in cands if e["ok"] and e["uploaded"]]
    for e in cands:
        if not e["ok"]:
            continue
        v = data[e["tag"]]
        if truth is not None and e["tag"] != "exact_local":
            e["vs_exact"] = bq_kit.corr2(v, truth) if len(v) == len(truth) else None
            e["vs_exact_r"] = pearson(v, truth)
        others = [bq_kit.corr2(v, data[u]) for u in up_names if u != e["tag"] and len(data[u]) == len(v)]
        e["vs_uploaded"] = max(others) if others else None
    matrix = [[None] * len(names) for _ in names]
    rmat = [[None] * len(names) for _ in names]
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                matrix[i][j] = 1.0
            elif len(data[a]) == len(data[b]):
                matrix[i][j] = bq_kit.corr2(data[a], data[b])
                rmat[i][j] = pearson(data[a], data[b])
    rows, _ = read_ledger()
    ops = info.get("count_ops") or {}
    return {
        "name": name, "qubits": info.get("num_qubits"), "ids": ids_of(name, info.get("num_qubits")),
        "depth_2q": info.get("depth_2q"), "depth_1q": info.get("depth_1q"), "count_ops": ops if isinstance(ops, dict) else {},
        "order": ops.get("x") if isinstance(ops, dict) else None, "points": info.get("points"),
        "candidates": cands, "matrix": {"names": names, "corr2": matrix, "r": rmat},
        "spend": tag_spend(rows, name), "uploads": subs, "max_uploads": MAX_UPLOADS,
    }


def jobs_view(limit):
    rows, ok = read_ledger()
    order = sorted(range(len(rows)), key=lambda k: (str(rows[k].get("ts") or ""), k), reverse=True)
    keep = ("ts", "circuit", "q", "tag", "device", "status", "value", "cost", "job_id")
    by_circuit = {}
    for r in rows:
        c = str(r.get("circuit"))
        e = by_circuit.setdefault(c, {"circuit": c, "jobs": 0, "pending": 0, "cost": 0.0})
        e["jobs"] += 1
        e["pending"] += is_pending(r)
        e["cost"] += num(r.get("cost"))
    return {
        "ok": ok, "total": len(rows), "spent": spent_of(rows),
        "status": dict(Counter(str(r.get("status") or "UNKNOWN") for r in rows)),
        "pending": sum(1 for r in rows if is_pending(r)),
        "by_circuit": sorted(by_circuit.values(), key=lambda e: natural(e["circuit"])),
        "by_tag": tag_spend(rows),
        "rows": [{k: rows[i].get(k) for k in keep} for i in order[:limit]],
    }


def doc_view(name):
    if name not in DOCS:
        raise Bad("Unknown document.")
    path = os.path.join(WORK, DOCS[name])
    try:
        st = os.stat(path)
        if st.st_size > 2_000_000:
            raise Refused("That file is too large to render.")
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return {"name": name, "path": "work/" + DOCS[name], "exists": False, "html": ""}
    return {"name": name, "path": "work/" + DOCS[name], "exists": True, "mtime": st.st_mtime, "html": md_to_html(text)}


def console_view(since):
    with LOCK:
        lines = [list(t) for t in CONSOLE if t[0] > since][:5000]
        last = _seq[0]
        cmds = [c.summary() for c in sorted(COMMANDS.values(), key=lambda c: -c.id)]
    return {"last": last, "lines": lines, "commands": cmds, "more": bool(lines) and lines[-1][0] < last}


def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if hasattr(o, "item"):  # numpy scalar
        return clean(o.item())
    return o


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "kit-dashboard"
    sys_version = ""

    def log_message(self, fmt, *args):  # keep the terminal quiet; polling would flood it
        pass

    def _send(self, code, body, ctype, extra=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(clean(obj)).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self):
        return (self.headers.get("Host") or "").strip().lower() in ALLOWED_HOSTS

    def _token_ok(self):
        return hmac.compare_digest((self.headers.get("X-Kit-Token") or "").encode(), SECRET.encode())

    def _page(self):
        try:
            with open(PAGE, encoding="utf-8") as f:
                page = f.read()
        except OSError:
            return self._send(500, b"kit/dashboard.html is missing", "text/plain; charset=utf-8")
        nonce = secrets.token_urlsafe(16)
        page = page.replace("__KIT_TOKEN__", SECRET).replace("__NONCE__", nonce)
        csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'unsafe-inline'; connect-src 'self'; "
               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % nonce)
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": csp})

    def _guard(self):
        if not self._host_ok():
            self._send(403, b"Forbidden: unexpected Host header", "text/plain; charset=utf-8")
            return None
        u = urlparse(self.path)
        if u.path.startswith("/api/") and not self._token_ok():
            self._json(403, {"error": "Missing or wrong dashboard token. Reload the page.", "token": True})
            return None
        return u

    def _dispatch(self, fn):
        try:
            self._json(200, fn())
        except Bad as ex:
            self._json(400, {"error": str(ex)})
        except Refused as ex:
            self._json(409, {"error": str(ex), **ex.extra})
        except Exception as ex:
            print(f"dashboard error: {type(ex).__name__}: {ex}", file=sys.stderr)
            self._json(500, {"error": f"Server error ({type(ex).__name__})."})

    def do_GET(self):
        u = self._guard()
        if u is None:
            return
        if u.path == "/":
            return self._page()
        if u.path == "/favicon.ico":
            return self._send(204, b"", "text/plain")
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        routes = {
            "/api/state": state,
            "/api/circuit": lambda: circuit_detail(q.get("name")),
            "/api/jobs": lambda: jobs_view(v_int(q.get("limit", "300"), 1, 1000000, "limit")),
            "/api/docs": lambda: doc_view(q.get("name")),
            "/api/console": lambda: console_view(v_int(q.get("since", "0"), 0, 10 ** 7, "since")),
        }
        if u.path not in routes:
            return self._json(404, {"error": "Not found."})
        self._dispatch(routes[u.path])

    def do_POST(self):
        u = self._guard()
        if u is None:
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return self._json(403, {"error": "Cross-origin request refused."})
        if (self.headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none"):
            return self._json(403, {"error": "Cross-site request refused."})
        if not u.path.startswith("/api/"):
            return self._json(404, {"error": "Not found."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "Send JSON."})
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if not 0 < length <= 65536:
            return self._json(413, {"error": "Request body missing or too large."})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Body is not valid JSON."})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Body must be a JSON object."})

        def cmd():
            name, args = body.get("cmd"), body.get("args") or {}
            if not isinstance(args, dict):
                raise Bad("args must be an object.")
            if name == "run":
                raise Refused("Paid runs go through the cost check and confirmation panel.")
            if name == "collect":
                return start_collect()
            c = launch(build_argv(name, args))
            return {"ok": True, "cmd": c.id}

        def cancel():
            nonce = body.get("nonce")
            if isinstance(nonce, str):
                with JOB_LOCK:
                    PENDING.pop(nonce, None)
            return {"ok": True}

        routes = {
            "/api/cmd": cmd,
            "/api/run/prepare": lambda: prepare_run(body),
            "/api/run/confirm": lambda: confirm_run(body),
            "/api/run/cancel": cancel,
            "/api/upload-log": lambda: log_upload(body),
        }
        if u.path not in routes:
            return self._json(404, {"error": "Not found."})
        self._dispatch(routes[u.path])


def main():
    global REHEARSAL, PORT
    p = argparse.ArgumentParser(description="Local control panel for the BlueQubit hackathon kit.")
    p.add_argument("--rehearsal", action="store_true", help="practice mode: children get BQ_MODE=rehearsal and an empty token")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    a = p.parse_args()
    if not 1024 <= a.port <= 65535:
        sys.exit("--port must be between 1024 and 65535")
    REHEARSAL = a.rehearsal or os.environ.get("BQ_MODE") == "rehearsal"
    if REHEARSAL:
        os.environ["BQ_MODE"] = "rehearsal"   # so bq_kit.cfg() in this process gives the rehearsal prefix and budget
    PORT = a.port
    ALLOWED_HOSTS.update({f"127.0.0.1:{PORT}", f"localhost:{PORT}"})
    ALLOWED_ORIGINS.update({f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as ex:
        sys.exit(f"Can't listen on 127.0.0.1:{PORT} ({ex.strerror}). Try --port {PORT + 1}.")
    server.daemon_threads = True
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Kit dashboard [{'REHEARSAL' if REHEARSAL else 'LIVE'}] at {url}  (Ctrl-C to stop)", flush=True)
    if not a.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with LOCK:
            jobs = [c for c in COMMANDS.values() if c.running and c.job]
        if jobs:
            print(f"`{jobs[0].label}` is still running. Waiting for it so the ledger stays intact. "
                  "Press Ctrl-C again to quit anyway (the run may stop early; `collect` recovers submitted jobs).")
            try:
                jobs[0].proc.wait()
                time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        print("Dashboard stopped.")


if __name__ == "__main__":
    main()
