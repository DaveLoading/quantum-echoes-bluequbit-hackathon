#!/usr/bin/env python3
"""bq_kit: the one door to BlueQubit for the whole agent team.

Every BlueQubit call goes through here so that spending, throttling, file names
and qubit order are handled the same way every time.

  python kit/bq_kit.py status
  python kit/bq_kit.py info S1 S2 G1 G2            # free: qubits, gate counts, depths
  python kit/bq_kit.py describe S1                 # structure of an OPEN sample circuit
  python kit/bq_kit.py exact S1                    # local exact answers for an open sample
  python kit/bq_kit.py estimate G1 --device mps.cpu --opt chi=128
  python kit/bq_kit.py estimate G1 --device pauli-path --opt th=1e-3
  python kit/bq_kit.py run G1 --device pauli-path --opt th=1e-3 --max-cost 2
  python kit/bq_kit.py collect                     # pick up results of jobs left running
  python kit/bq_kit.py score work/csv/S1__exact_local.csv work/csv/S1__pauli-path_th0.01.csv
  python kit/bq_kit.py blend G3 work/csv/G3__a.csv work/csv/G3__b.csv --weights 2,1
  python kit/bq_kit.py validate G1 work/csv/G1__mps.cpu_chi128.csv
  python kit/bq_kit.py candidates G1               # every CSV for G1, ranked by how they agree

The API token is read from the BLUEQUBIT_API_TOKEN environment variable by the
SDK. This file never prints, logs or stores it.
Set BQ_MODE=rehearsal to use the offline practice backend (no account, no cost).
"""
import argparse
import csv
import glob
import json
import math
import os
import sys
import signal
import time
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
CSV_DIR = os.path.join(WORK, "csv")
QASM_DIR = os.path.join(WORK, "qasm")          # unzip sample QASM here: work/qasm/S1/*.qasm
LEDGER = os.path.join(WORK, "jobs.jsonl")
CIRCUITS = os.path.join(WORK, "circuits.json")
CONFIG = os.path.join(ROOT, "config.json")

OPT_ALIASES = {
    "chi": "mps_bond_dimension",
    "th": "pauli_path_truncation_threshold",
    "level": "pauli_path_circuit_transpilation_level",
    "wcut": "pauli_path_weight_cutoff",
}
RETRY_STATUSES = {"HACKATHON_PARALLEL_JOBS_LIMIT", "HACKATHON_GPU_QUOTA_COOLDOWN", "JOBS_LIMIT_EXCEEDED"}
IN_PROGRESS = {"PENDING", "QUEUED", "RUNNING"}   # the SDK's JOB_NON_TERMINAL_STATES
LOCK_FILE = os.path.join(WORK, ".lock")
LOCK_WAIT_S = 10


# ------------------------------------------------------------------ config
def cfg():
    with open(CONFIG) as f:
        c = json.load(f)
    if os.environ.get("BQ_MODE") == "rehearsal":
        c["prefix"] = c.get("rehearsal_prefix", "Rehearsal")
        c["budget_usd"] = c.get("rehearsal_budget_usd", c["budget_usd"])
    return c


def cfg_num(key, default):
    """Optional numeric config.json setting (poll_seconds, wait_timeout_minutes,
    submit_retry_minutes). Missing or bad values give the default."""
    try:
        v = float(cfg().get(key, default))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def circuit_id(c, q):
    return f"{cfg()['prefix']}_{c}_{q}"


def id_count(c, nq):
    """How many IDs circuit c has: config.json "ids" (e.g. {"G4": 36}) when set, else one
    per qubit. The Pauli label always has num_qubits letters; only the ID count changes."""
    o = (cfg().get("ids") or {}).get(c)
    return int(o) if o is not None else nq


def num_ids(bq, c):
    return id_count(c, num_qubits(bq, c))


def zstring(n, q):
    """Pauli Z on qubit q. Qubit 0 is the RIGHTMOST letter. Never change this."""
    return "I" * (n - 1 - q) + "Z" + "I" * q


def client():
    if os.environ.get("BQ_MODE") == "rehearsal":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fake_bq import FakeBQ
        return FakeBQ()
    import bluequbit
    if not os.environ.get("BLUEQUBIT_API_TOKEN"):
        sys.exit("BLUEQUBIT_API_TOKEN is not set. Export it in your shell (never paste it into chat).")
    return bluequbit.init()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_opts(pairs):
    opts = {}
    for p in pairs or []:
        k, v = p.split("=", 1)
        k = OPT_ALIASES.get(k, k)
        opts[k] = int(v) if k in ("mps_bond_dimension", "pauli_path_circuit_transpilation_level") else float(v)
    return opts


def tag_for(device, opts):
    short = {v: k for k, v in OPT_ALIASES.items()}
    parts = [device] + [f"{short.get(k, k)}{v:g}" if isinstance(v, float) else f"{short.get(k, k)}{v}" for k, v in sorted(opts.items())]
    return "_".join(parts)


# ------------------------------------------------------------------ ledger
def ledger_rows():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER) as f:
        return [json.loads(line) for line in f if line.strip()]


def ledger_add(row):
    os.makedirs(WORK, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def ledger_rewrite(rows):
    """Atomic: write a temp file next to the ledger, then rename it over the old one, so a
    reader or a kill mid-write never sees a half-written ledger."""
    os.makedirs(WORK, exist_ok=True)
    tmp = f"{LEDGER}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, LEDGER)


_SLOT = []


def take_job_slot():
    """run and collect hold an exclusive lock on work/.lock for their whole run: one
    job-submitting command at a time (rule 5), and no two writers on the ledger."""
    import fcntl
    os.makedirs(WORK, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.time() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= deadline:
                os.close(fd)
                sys.exit(f"STOPPED: another run or collect holds the job slot (work/.lock); waited {LOCK_WAIT_S}s. "
                         "Nothing submitted. Wait for it to finish, then run this again.")
            time.sleep(0.25)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()} {' '.join(sys.argv[1:3])}\n".encode())
    _SLOT.append(fd)  # released when this process exits


def spent():
    return round(sum(r.get("cost") or 0.0 for r in ledger_rows()), 4)


def _usd(x):
    """Dollars, with enough digits that small rehearsal amounts don't round to $0.00."""
    return f"${x:.2f}" if abs(x) >= 0.1 or x == 0 else f"${x:.4f}"


def _dead(status):
    """A job that will never report a value: any status that is set and isn't in progress
    (PENDING, QUEUED, RUNNING) or COMPLETED. Covers FAILED_VALIDATION, CANCELED, TERMINATED,
    NOT_ENOUGH_FUNDS, JOBS_LIMIT_EXCEEDED and the HACKATHON_* limit states."""
    s = str(status or "").upper()
    return bool(s) and s not in IN_PROGRESS and s != "COMPLETED"


def _settled(jr):
    return jr.run_status == "COMPLETED" or _dead(jr.run_status)


def _value_of(jr):
    """(value, problem). Unwraps a 1-item list; anything else that isn't one number is a problem."""
    v = getattr(jr, "expectation_value", None)
    if v is None:
        return None, None
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if isinstance(v, (list, tuple)) and len(v) == 1:
        v = v[0]
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        return None, f"expectation_value is not one number: {str(v)[:80]}"
    return float(v), None


def _apply(r, jr):
    """Copy a fetched job onto its ledger row. A non-scalar value is stored as FAILED with the reason."""
    v, problem = _value_of(jr)
    r["status"], r["value"], r["cost"] = jr.run_status, v, jr.cost
    if problem:
        r["status"], r["error"] = "FAILED", problem
        print(f"  job {r['job_id']} (qubit {r['q']}): {problem}. Stored as FAILED.")


def _real_costs(rows=None):
    """Real per-job costs of finished jobs, grouped by (circuit, tag)."""
    out = {}
    for r in rows if rows is not None else ledger_rows():
        if r.get("status") == "COMPLETED" and r.get("cost") is not None:
            out.setdefault((r["circuit"], r["tag"]), []).append(r["cost"])
    return out


def uncollected(bq=None):
    """Jobs submitted with --no-wait that `collect` hasn't priced yet: (count,
    projected $, count with no cost data, count priced at the estimate). Projected at the
    real per-qubit cost of the same setting where known, else (when a client is given) at
    BlueQubit's estimate, fetched once per setting. spent() can't see these, so budget checks add them."""
    rows = ledger_rows()
    known = _real_costs(rows)
    n, total, blind, by_est = 0, 0.0, 0, 0
    for r in rows:
        if r.get("cost") is None and not _dead(r.get("status")):
            n += 1
            k = known.get((r["circuit"], r["tag"]))
            est = None
            if not k and bq is not None:
                est = _estimate(bq, r["circuit"], r.get("device") or r["tag"].split("_")[0], r.get("options") or {})
            if k:
                total += float(np.mean(k))
            elif est is not None:
                total += est
                by_est += 1
            else:
                blind += 1
    return n, round(total, 4), blind, by_est


def _bq_estimate(bq, circuit, device, opts, q=0):
    """BlueQubit's estimate for one ID of `circuit`, sent with the same pauli_sum and options a
    run would send. The SDK's estimate() (0.19.1b1) takes only circuit and device, so
    pauli-path refuses it ("requires the pauli_sum parameter") and chi/th never reach it. The
    same POST /jobs with estimate_only=True does take both, so the live client sends that.
    `q` picks the ID (and its Z observable): Pauli-path's threshold floor differs per ID."""
    cid = circuit_id(circuit, q)
    conn = getattr(bq, "_backend_connection", None)
    if conn is None:  # rehearsal and test stand-ins
        return bq.estimate(cid, device=device)
    from bluequbit.api import jobs
    from bluequbit.estimate_result import EstimateResult
    device = bq.validate_device(device)
    bq._validate_options(opts or None, device)
    pauli = [(zstring(num_qubits(bq, circuit), q), 1.0)]
    return EstimateResult(jobs.submit_jobs(conn, cid, device, pauli_sum=pauli, options=opts or None,
                                           estimate_only=True))


_EST = {}


def _estimate(bq, circuit, device, opts, q=0):
    """Per-qubit estimate for this setting (priced on ID q), fetched once per command. None when unavailable."""
    key = (circuit, tag_for(device, opts or {}), q)
    if key not in _EST:
        _EST[key] = None
        try:
            e = _bq_estimate(bq, circuit, device, opts, q)
            if not e.error_message and e.estimated_cost is not None:
                _EST[key] = float(e.estimated_cost)
        except Exception:
            pass
    return _EST[key]


def per_qubit_cost(bq, circuit, tag, device, opts=None, q=0):
    """Best guess at one qubit's cost for this exact setting: the mean real cost of
    finished jobs at this setting, else BlueQubit's estimate (a floor: it may ignore chi/th)."""
    seen = _real_costs().get((circuit, tag))
    if seen:
        return float(np.mean(seen)), f"real cost of {len(seen)} finished jobs at this setting"
    est = _estimate(bq, circuit, device, opts, q)
    if est is not None:
        return est, f"BlueQubit estimate only (priced on q{q}), a floor that may ignore chi/th"
    return None, "no cost data"


def circuits_db():
    if os.path.exists(CIRCUITS):
        with open(CIRCUITS) as f:
            return json.load(f)
    return {}


def num_qubits(bq, c):
    db = circuits_db()
    if c in db:
        return db[c]["num_qubits"]
    return bq.get_circuit_info(circuit_id(c, 0)).num_qubits


# ------------------------------------------------------------------ csv
def write_csv(path, c, values):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["circuit_id", "value"])
        for q, v in enumerate(values):
            w.writerow([circuit_id(c, q), repr(float(v))])


def read_csv(path):
    vals = {}
    with open(path) as f:
        for row in csv.reader(f):
            if not row or row[0] == "circuit_id":
                continue
            vals[row[0]] = float(row[1])
    q_of = lambda cid: int(cid.rsplit("_", 1)[1])
    return np.array([vals[k] for k in sorted(vals, key=q_of)])


def corr2(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) != len(b) or a.std() == 0 or b.std() == 0:
        return 0.0
    r = float(np.corrcoef(a, b)[0, 1])
    return r * r if r > 0 else 0.0


def zscore(v):
    v = np.asarray(v, float)
    return (v - v.mean()) / v.std() if v.std() > 0 else v * 0


# ------------------------------------------------------------------ commands
def cmd_status(_a):
    c = cfg()
    db = circuits_db()
    n_un, un_cost, un_blind, _ = uncollected()
    extra = f" (+${un_cost:.3f} projected for {n_un} uncollected jobs{f', {un_blind} unpriced' if un_blind else ''})" if n_un else ""
    print(f"mode={os.environ.get('BQ_MODE', 'live')}  prefix={c['prefix']}  spent=${spent():.3f}{extra} of ${c['budget_usd']:.2f} budget")
    names = sorted(set(list(db) + [os.path.basename(p).split("__")[0] for p in glob.glob(os.path.join(CSV_DIR, "*.csv"))]))
    if not names:
        print("No circuits yet. Run: python kit/bq_kit.py info <names>")
    for n in names:
        files = sorted(glob.glob(os.path.join(CSV_DIR, f"{n}__*.csv")))
        info = db.get(n, {})
        nq, ni = info.get('num_qubits'), id_count(n, info.get('num_qubits'))
        ids = f" ({ni} IDs)" if ni is not None and ni != nq else ""
        print(f"\n{n}: {nq if nq is not None else '?'} qubits{ids}, 2q depth {info.get('depth_2q', '?')}, points {info.get('points', '?')}")
        for f in files:
            print(f"   {os.path.basename(f)}")
    # a --no-wait job can already say COMPLETED with no value until `collect` fetches it
    pending = [r for r in ledger_rows() if not _dead(r.get("status")) and r.get("value") is None]
    if pending:
        print(f"\n{len(pending)} jobs still pending. Run: python kit/bq_kit.py collect")


def cmd_info(a):
    bq = client()
    db = circuits_db()
    for c in a.circuits:
        info = bq.get_circuit_info(circuit_id(c, 0))
        db[c] = {
            "num_qubits": info.num_qubits,
            "num_ids": id_count(c, info.num_qubits),
            "count_ops": dict(info.count_ops),
            "depth_1q": info.depth_1q,
            "depth_2q": info.depth_2q,
            "points": db.get(c, {}).get("points"),
        }
        ids = db[c]["num_ids"]
        print(f"{c}: {info.num_qubits} qubits{f' ({ids} IDs)' if ids != info.num_qubits else ''}, 2q depth {info.depth_2q}, 1q depth {info.depth_1q}, ops {dict(info.count_ops)}")
    with open(CIRCUITS, "w") as f:
        json.dump(db, f, indent=2)


def cmd_points(a):
    db = circuits_db()
    db.setdefault(a.circuit, {})["points"] = a.points
    with open(CIRCUITS, "w") as f:
        json.dump(db, f, indent=2)
    print(f"{a.circuit}: {a.points} points")


def _qasm_files(c):
    files = glob.glob(os.path.join(QASM_DIR, c, "*.qasm"))
    if not files:
        sys.exit(f"No QASM for {c}. Unzip the sample to work/qasm/{c}/ first.")
    return {int(os.path.basename(p).rsplit("_", 1)[1].split(".")[0]): p for p in files}


def cmd_describe(a):
    from sim_local import describe, load_qasm
    files = _qasm_files(a.circuit)
    for q in sorted(files)[: a.show]:
        print(f"--- {os.path.basename(files[q])}")
        print(json.dumps(describe(load_qasm(files[q])), indent=1))


def cmd_exact(a):
    from sim_local import exact_z, load_qasm
    files = _qasm_files(a.circuit)
    vals = []
    for q in range(len(files)):
        vals.append(exact_z(load_qasm(files[q]), q))
    out = os.path.join(CSV_DIR, f"{a.circuit}__exact_local.csv")
    write_csv(out, a.circuit, vals)
    print(f"wrote {out}")


def cmd_local(a):
    """Local approximate run on an open sample (free). Useful to learn trends."""
    from sim_local import load_qasm, mps_z, pauli_path_z
    files = _qasm_files(a.circuit)
    vals = []
    for q in range(len(files)):
        qc = load_qasm(files[q])
        vals.append(mps_z(qc, q, a.chi) if a.method == "mps" else pauli_path_z(qc, q, a.th))
    tag = f"local-mps_chi{a.chi}" if a.method == "mps" else f"local-pp_th{a.th:g}"
    out = os.path.join(CSV_DIR, f"{a.circuit}__{tag}.csv")
    write_csv(out, a.circuit, vals)
    print(f"wrote {out}")


def cmd_estimate(a):
    bq = client()
    n = num_ids(bq, a.circuit)  # one job per ID
    try:
        e = _bq_estimate(bq, a.circuit, a.device, parse_opts(a.opt), getattr(a, "qubit", 0) or 0)
        if e.error_message:
            print(f"estimate failed: {e.error_message}")
            return None
        per = e.estimated_cost or 0.0
        print(f"{a.circuit} on {a.device}: about ${per:.4f} and {e.estimated_runtime} ms per qubit; x{n} qubits = about ${per * n:.2f}")
        if e.warning_message:
            print(f"warning: {e.warning_message}")
        print("Note: the estimate may ignore options like chi or threshold. Treat it as a floor.")
        return per * n
    except Exception as ex:  # estimate isn't offered for every device
        print(f"no estimate available for {a.device} ({type(ex).__name__}). Run 3 qubits with --qubits 0-2 and check cost.")
        return None


def _qubit_list(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out += list(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return [q for q in out if q < n]


class SlotBusy(Exception):
    """BlueQubit kept refusing a submit at a parallel-limit status for the whole retry window."""


def _submit(bq, cid, n, q, device, opts, in_flight=None, retry=True):
    """Submit one job. At a parallel-limit status, print the status and BlueQubit's message, then
    wait and retry (5 s, 10 s, ... capped at 30 s) for up to submit_retry_minutes (default 30);
    then raise SlotBusy. With retry=False (--no-wait), raise SlotBusy at the first refusal."""
    window = cfg_num("submit_retry_minutes", 30) * 60
    start, tries = time.time(), 0
    while True:
        try:
            return bq.run(cid, device=device, pauli_sum=[(zstring(n, q), 1.0)],
                          options=opts or None, asynchronous=True)
        except Exception as ex:
            status = str(getattr(ex, "run_status", "") or "")
            hit = next((s for s in RETRY_STATUSES if s in (status or str(ex))), None)
            if not hit:
                raise
            used = time.time() - start
            detail = str(getattr(ex, "error_message", "") or "").strip()
            busy = f" with {in_flight} ledger jobs pending or running" if in_flight is not None else ""
            said = f"  submit of qubit {q} refused: status {status or hit}{busy}" + (f" ({detail[:300]})" if detail else "")
            if not retry:
                print(said + ".", flush=True)
                raise SlotBusy(f"BlueQubit answered {hit}")
            if used >= window:
                print(said + ".", flush=True)
                raise SlotBusy(f"BlueQubit still answers {hit} after {used / 60:.1f} min of retrying")
            tries += 1
            pause = max(1.0, min(30.0, 5.0 * tries, window - used))
            print(said + f". Waiting {pause:.0f}s, then retrying ({used / 60:.1f} of {window / 60:g} min used).", flush=True)
            time.sleep(pause)


def _finish(bq, jobs):
    """Poll each job with bq.get every poll_seconds (default 5) until every one is COMPLETED or
    dead. (bq.wait raises at the first dead job and strands its siblings.) Gives up after
    wait_timeout_minutes (default 30). Returns (latest results, ids still unfinished)."""
    poll = cfg_num("poll_seconds", 5)
    deadline = time.time() + cfg_num("wait_timeout_minutes", 30) * 60
    latest = {j.job_id: j for j in jobs}
    fetched, start, beat = set(), time.time(), time.time()
    while True:
        for jid in list(latest):
            if jid in fetched and _settled(latest[jid]):
                continue
            try:
                latest[jid] = bq.get(jid)
                fetched.add(jid)
            except Exception:
                pass  # keep the last known state; try again next poll
        left = [jid for jid in latest if not (jid in fetched and _settled(latest[jid]))]
        if not left or time.time() >= deadline:
            return [latest[j.job_id] for j in jobs], left
        if time.time() - beat >= 60:
            beat = time.time()
            print(f"  waiting: {len(left)} of {len(jobs)} jobs still running ({(beat - start) / 60:.0f} min)", flush=True)
        time.sleep(poll)


_SETTLED = set()


def _busy_jobs(bq):
    """Ledger jobs still pending or running on BlueQubit, polled with bq.get. They hold
    BlueQubit's parallel slots whichever run submitted them. A job that can't be fetched
    isn't counted: BlueQubit's own limit refusal still backs this up."""
    out = []
    for r in ledger_rows():
        jid = r.get("job_id")
        if not jid or jid in _SETTLED or r.get("value") is not None or _dead(r.get("status")):
            continue
        try:
            jr = bq.get(jid)
        except Exception:
            continue
        if _settled(jr):
            _SETTLED.add(jid)
        else:
            out.append(jid)
    return out


def _wait_for_slot(bq, limit):
    """Wait until fewer than `limit` ledger jobs are pending or running. Returns that count,
    or None after wait_timeout_minutes (default 30)."""
    poll, mins = cfg_num("poll_seconds", 5), cfg_num("wait_timeout_minutes", 30)
    start = beat = time.time()
    said = False
    while True:
        busy = len(_busy_jobs(bq))
        if busy < limit:
            return busy
        if time.time() - start >= mins * 60:
            return None
        if not said or time.time() - beat >= 60:
            beat = time.time()
            print(f"  {busy} jobs pending or running (max_parallel {limit}): waiting for a slot"
                  + (f" ({(beat - start) / 60:.0f} of {mins:g} min)" if said else ""), flush=True)
            said = True
        time.sleep(poll)


def cmd_run(a):
    take_job_slot()
    c = cfg()
    bq = client()
    opts = parse_opts(a.opt)
    tag = tag_for(a.device, opts)
    n = num_qubits(bq, a.circuit)          # Pauli label length: always num_qubits
    n_ids = id_count(a.circuit, n)         # jobs: one per ID
    qubits = _qubit_list(a.qubits, n_ids)
    if not a.force:  # never pay twice: skip qubits this exact setting already finished
        mine = [r for r in ledger_rows() if r["circuit"] == a.circuit and r["tag"] == tag]
        have = {r["q"] for r in mine if r.get("status") == "COMPLETED" and r.get("value") is not None}
        waiting = {r["q"] for r in mine if not _dead(r.get("status")) and r.get("value") is None} - have
        skipped = [q for q in qubits if q in have]
        held = [q for q in qubits if q in waiting]
        qubits = [q for q in qubits if q not in have and q not in waiting]
        if skipped:
            print(f"skipping {len(skipped)} qubits already done for this setting (use --force to rerun)")
        if held:
            print(f"skipping {len(held)} qubits submitted earlier and not collected yet (run `collect`; --force resubmits and pays again)")
        if not qubits:
            _build_csv(a.circuit, tag, n_ids)
            return
    n_un, un_cost, un_blind, un_est = uncollected(bq)
    budget_left = c["budget_usd"] - spent() - un_cost   # count jobs still waiting for `collect`
    cap = a.max_cost if a.max_cost is not None else c.get("default_max_cost_per_run", 2.0)
    if n_un:
        print(f"note: {n_un} uncollected jobs, projected {_usd(un_cost)}, counted against the budget"
              + (f" ({un_est} priced at the BlueQubit estimate)" if un_est else "")
              + (f"; {un_blind} have no cost data yet, so the real total may be higher" if un_blind else "")
              + ". Run `collect` for real costs.")
    if budget_left <= 0:
        sys.exit(f"Budget used up ({_usd(spent())} spent + {_usd(un_cost)} uncollected, of ${c['budget_usd']:.2f}). Ask David before spending more.")
    if a.no_wait:
        # Without waiting, no job reports its cost during the run, so the cap is enforced
        # up front: project the run at the per-qubit cost and submit only what fits.
        per_q, src = per_qubit_cost(bq, a.circuit, tag, a.device, opts, q=qubits[0])
        if per_q is None:
            sys.exit("--no-wait needs a cost per qubit and there's none for this setting yet. "
                     "Run 3 qubits first (--qubits 0-2), collect, then submit the rest with --no-wait.")
        limit = min(cap, budget_left)
        print(f"--no-wait: about ${per_q:.4f} per qubit ({src}); {len(qubits)} qubits projected at {_usd(per_q * len(qubits))}")
        if per_q > 0 and per_q * len(qubits) > limit:
            fit = int(limit / per_q + 1e-9)
            what = f"{_usd(cap)} cap" if cap <= budget_left else f"{_usd(budget_left)} budget left"
            if fit == 0:
                sys.exit(f"STOPPED: one qubit (about ${per_q:.4f}) is over the {what}. Nothing submitted.")
            print(f"STOPPED at the {what}: submitting {fit} of {len(qubits)} qubits (projected {_usd(per_q * fit)}). "
                  f"Collect, then rerun the same command for the rest.")
            qubits = qubits[:fit]
        if src.startswith("BlueQubit estimate"):
            print("warning: the projection uses the estimate, not real costs. Safer: submit 3 qubits, collect, then the rest.")
    print(f"{a.circuit} {tag}: {len(qubits)} jobs, run cap {_usd(cap)}, budget left {_usd(budget_left)}")
    run_cost, run_est, done, sent = 0.0, 0.0, 0, 0
    chunk = max(1, int(c.get("max_parallel", 8)))
    todo = list(qubits)
    if a.no_wait:
        # Never more than max_parallel jobs pending or running: submit what fits in the free
        # slots now, then stop. No sleeping: rerun after `collect` for the rest.
        busy = len(_busy_jobs(bq))
        for i, q in enumerate(todo):
            if busy >= chunk:
                busy = len(_busy_jobs(bq))  # one fresh count, no sleeping
            if busy >= chunk:
                print(f"{sent} submitted, {len(todo) - i} left: rerun after collect "
                      f"(max_parallel {chunk}, {busy} jobs pending or running).")
                break
            try:
                j = _submit(bq, circuit_id(a.circuit, q), n, q, a.device, opts, in_flight=busy, retry=False)
            except SlotBusy:
                print(f"{sent} submitted, {len(todo) - i} left: rerun after collect "
                      f"(BlueQubit is at its parallel limit; max_parallel in config.json is {chunk}).")
                break
            busy += 1
            ledger_add({"ts": now(), "circuit": a.circuit, "q": q, "tag": tag, "device": a.device,
                        "options": opts, "job_id": j.job_id, "status": j.run_status, "value": None, "cost": None})
            sent += 1
        print("Submitted without waiting. Run `collect` later to gather results.")
        return
    # Finished jobs at this setting that came back with no cost: the cap runs on the estimate.
    blind = any(r["circuit"] == a.circuit and r["tag"] == tag and r.get("status") == "COMPLETED"
                and r.get("value") is not None and r.get("cost") is None for r in ledger_rows())
    blind_said = False
    while todo:
        # Size each batch so it can't carry the run past the cap or the budget. Price the
        # next qubit from real costs at this setting; with none yet, run 1 qubit to learn it.
        room = min(cap, budget_left) - run_cost - run_est
        seen = _real_costs().get((a.circuit, tag))
        if seen:
            per_q = float(np.mean(seen))
            size = chunk if per_q <= 0 else max(0, min(chunk, int(room / per_q + 1e-9)))
            if size == 0:
                print(f"STOPPED: the next qubit (about ${per_q:.4f}) would pass the limit "
                      f"({_usd(max(room, 0))} left under the {_usd(cap)} cap and the budget). {len(todo)} qubits not run.")
                break
        elif blind:
            est = _estimate(bq, a.circuit, a.device, opts)
            if not blind_said:
                blind_said = True
                print("  BlueQubit reports no cost for finished jobs at this setting: the cap is running on the estimate"
                      + (f" (about ${est:.4f} per qubit), so real spend may differ." if est is not None
                         else ", and there's no estimate either, so the cap can't see this run's spend."), flush=True)
            size = chunk if not est or est <= 0 else max(0, min(chunk, int(room / est + 1e-9)))
            if size == 0:
                print(f"STOPPED: the next qubit (about ${est:.4f}, estimate) would pass the limit "
                      f"({_usd(max(room, 0))} left under the {_usd(cap)} cap and the budget). {len(todo)} qubits not run.")
                break
        else:
            est, _ = per_qubit_cost(bq, a.circuit, tag, a.device, opts, q=todo[0])  # estimate only, since nothing has finished
            if est is not None and est > room:
                print(f"STOPPED: one qubit (about ${est:.4f}, estimate) is over the {_usd(max(room, 0))} left. {len(todo)} qubits not run.")
                break
            size = 1
            print("  no real price for this setting yet: running 1 qubit first to learn it")
        part = todo[:size]
        todo = todo[len(part):]
        jobs, busy, n_busy = [], None, None
        for q in part:
            if n_busy is None or n_busy >= chunk:
                n_busy = _wait_for_slot(bq, chunk)
                if n_busy is None:
                    busy = f"no free slot after {cfg_num('wait_timeout_minutes', 30):g} min (max_parallel {chunk})"
                    todo = part[len(jobs):] + todo
                    break
            try:
                j = _submit(bq, circuit_id(a.circuit, q), n, q, a.device, opts, in_flight=n_busy)
            except SlotBusy as ex:
                busy = ex
                todo = part[len(jobs):] + todo
                break
            n_busy += 1
            jobs.append(j)
            ledger_add({"ts": now(), "circuit": a.circuit, "q": q, "tag": tag, "device": a.device,
                        "options": opts, "job_id": j.job_id, "status": j.run_status, "value": None, "cost": None})
        sent += len(jobs)
        results, left = _finish(bq, jobs) if jobs else ([], [])
        rows = ledger_rows()
        by_id = {r.job_id: r for r in results}
        fill = None
        for r in rows:
            jr = by_id.get(r["job_id"])
            if jr is not None:
                _apply(r, jr)
                if r["status"] == "COMPLETED":
                    done += 1
                    if r["cost"] is None and r["value"] is not None:
                        blind = True  # priced from the estimate from now on
                        if fill is None:
                            known = _real_costs(rows).get((a.circuit, tag))
                            fill = float(np.mean(known)) if known else (_estimate(bq, a.circuit, a.device, opts) or 0.0)
                        run_est += fill
                run_cost += r["cost"] or 0.0
        ledger_rewrite(rows)
        print(f"  {sent}/{len(qubits)} submitted, {done} done, run cost ${run_cost:.4f}"
              + (f" + about ${run_est:.4f} estimated for jobs with no reported cost" if run_est else ""))
        if left:
            print(f"STOPPED: {len(left)} jobs still unfinished after {cfg_num('wait_timeout_minutes', 30):g} min. "
                  f"They stay in the ledger: run `collect` later, then the same command for the rest. {len(todo)} qubits not run.")
            break
        if busy:
            print(f"STOPPED: {busy}. {len(todo)} qubits not run. Rerun the same command later (finished qubits are skipped).")
            break
        if spent() + un_cost + run_est >= c["budget_usd"]:
            print("STOPPED: total budget reached.")
            break
    _build_csv(a.circuit, tag, n_ids)


def cmd_build(a):
    bq = client()
    _build_csv(a.circuit, a.tag, num_ids(bq, a.circuit), fill=a.fill_missing)


def _build_csv(c, tag, n, fill=False):
    """n is the number of IDs (num_ids), which can be fewer than the qubits."""
    latest = {}
    for r in ledger_rows():
        if r["circuit"] == c and r["tag"] == tag and r.get("status") == "COMPLETED" and r.get("value") is not None:
            v = r["value"][0] if isinstance(r["value"], list) and len(r["value"]) == 1 else r["value"]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and r["q"] < n:
                latest[r["q"]] = v
    missing = [q for q in range(n) if q not in latest]
    if missing and fill and latest:
        mean = float(np.mean(list(latest.values())))
        for q in missing:
            latest[q] = mean
        print(f"{c} {tag}: filled {len(missing)} missing qubits with the mean {mean:.3f}")
        tag = tag + "_partial"
        missing = []
    if missing:
        print(f"{c} {tag}: {len(missing)} of {n} qubits missing ({missing[:10]}{'...' if len(missing) > 10 else ''}). No CSV written.")
        return None
    out = os.path.join(CSV_DIR, f"{c}__{tag}.csv")
    write_csv(out, c, [latest[q] for q in range(n)])
    print(f"wrote {out}")
    return out


def cmd_collect(_a):
    take_job_slot()
    bq = client()
    rows = ledger_rows()
    changed = set()
    for r in rows:
        if _dead(r.get("status")):
            continue  # a dead job never changes
        if r.get("status") == "COMPLETED" and r.get("value") is not None and r.get("cost") is not None:
            continue
        try:
            jr = bq.get(r["job_id"])
        except Exception as ex:
            print(f"could not fetch {r['job_id']}: {type(ex).__name__}")
            continue
        before = (r.get("status"), r.get("value"), r.get("cost"))
        _apply(r, jr)
        if (r["status"], r["value"], r["cost"]) != before or r["status"] != "COMPLETED":
            changed.add((r["circuit"], r["tag"]))
    ledger_rewrite(rows)
    for c, tag in sorted(changed):
        _build_csv(c, tag, num_ids(bq, c))
    nocost = sum(1 for r in rows if r.get("status") == "COMPLETED" and r.get("value") is not None and r.get("cost") is None)
    if nocost:
        print(f"note: {nocost} finished jobs still report no cost; budget checks price them at the estimate.")
    print(f"collected. spent=${spent():.2f}")


def cmd_score(a):
    x, y = read_csv(a.a), read_csv(a.b)
    print(f"corr2 = {corr2(x, y):.4f}   ({os.path.basename(a.a)} vs {os.path.basename(a.b)})")


def cmd_blend(a):
    arrs = [read_csv(f) for f in a.files]
    w = [float(x) for x in a.weights.split(",")] if a.weights else [1.0] * len(arrs)
    if len({len(x) for x in arrs}) != 1:
        sys.exit("files have different qubit counts")
    mix = sum(wi * zscore(x) for wi, x in zip(w, arrs)) / sum(w)
    name = a.name or "blend_" + "+".join(os.path.basename(f).split("__", 1)[1].rsplit(".", 1)[0] for f in a.files)
    out = os.path.join(CSV_DIR, f"{a.circuit}__{name}.csv")
    write_csv(out, a.circuit, mix)
    print(f"wrote {out}")


def cmd_validate(a):
    prefix = cfg()["prefix"]
    rows = []
    if not os.path.exists(a.file):
        sys.exit(f"NOT READY: {a.file} doesn't exist. Run `status` to see which CSVs exist.")
    with open(a.file) as f:
        for row in csv.reader(f):
            if row and row[0] != "circuit_id":
                rows.append(row)
    problems = []
    db = circuits_db()
    n = id_count(a.circuit, db.get(a.circuit, {}).get("num_qubits"))
    ids = [r[0] for r in rows]
    if n is not None:
        want = {f"{prefix}_{a.circuit}_{q}" for q in range(n)}
        if set(ids) != want:
            problems.append(f"IDs don't match: {len(set(ids) - want)} unexpected, {len(want - set(ids))} missing")
    if len(ids) != len(set(ids)):
        problems.append("duplicate IDs")
    vals = []
    for r in rows:
        try:
            v = float(r[1])
            if not math.isfinite(v):
                problems.append(f"non-finite value for {r[0]}")
            vals.append(v)
        except ValueError:
            problems.append(f"not a number for {r[0]}")
    if vals and np.std(vals) == 0:
        problems.append("all values equal: scores 0")
    if problems:
        print("NOT READY:\n - " + "\n - ".join(problems))
        sys.exit(1)
    print(f"READY to upload: {len(rows)} rows, IDs {prefix}_{a.circuit}_0..{len(rows) - 1}, values {min(vals):.3f} to {max(vals):.3f}")


def cmd_candidates(a):
    files = sorted(glob.glob(os.path.join(CSV_DIR, f"{a.circuit}__*.csv")))
    if not files:
        sys.exit(f"no CSVs for {a.circuit}")
    data = {os.path.basename(f).split("__", 1)[1][:-4]: read_csv(f) for f in files}
    names = list(data)
    truth = data.get("exact_local")
    print(f"{a.circuit}: {len(names)} candidate(s)")
    if truth is not None:
        print("vs exact_local (samples only):")
        for k in names:
            if k != "exact_local":
                print(f"  {corr2(data[k], truth):.4f}  {k}")
    if len(names) > 1:
        print("agreement between candidates (corr2; > 0.98 suggests converged):")
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                print(f"  {corr2(data[names[i]], data[names[j]]):.4f}  {names[i]}  vs  {names[j]}")


def cmd_rehearsal_score(a):
    """Rehearsal only: plays the website's scorer for practice 'hidden' circuits."""
    if os.environ.get("BQ_MODE") != "rehearsal":
        sys.exit("rehearsal-score only works with BQ_MODE=rehearsal")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fake_bq import hidden_truth
    print(f"{a.circuit}: corr2 = {corr2(read_csv(a.file), hidden_truth(a.circuit)):.4f}  (the site would show this)")


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet when piped to head
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("status").set_defaults(f=cmd_status)
    x = s.add_parser("info"); x.add_argument("circuits", nargs="+"); x.set_defaults(f=cmd_info)
    x = s.add_parser("points"); x.add_argument("circuit"); x.add_argument("points", type=int); x.set_defaults(f=cmd_points)
    x = s.add_parser("describe"); x.add_argument("circuit"); x.add_argument("--show", type=int, default=2); x.set_defaults(f=cmd_describe)
    x = s.add_parser("exact"); x.add_argument("circuit"); x.set_defaults(f=cmd_exact)
    x = s.add_parser("local"); x.add_argument("circuit"); x.add_argument("--method", choices=["mps", "pp"], default="pp")
    x.add_argument("--chi", type=int, default=32); x.add_argument("--th", type=float, default=1e-2); x.set_defaults(f=cmd_local)
    x = s.add_parser("estimate"); x.add_argument("circuit"); x.add_argument("--device", required=True)
    x.add_argument("--opt", action="append", help="chi=128 | th=1e-3 | level=2 (sent with the estimate)")
    x.add_argument("--qubit", type=int, default=0, help="price this ID (default 0); pauli-path floors differ per ID"); x.set_defaults(f=cmd_estimate)
    x = s.add_parser("run"); x.add_argument("circuit"); x.add_argument("--device", required=True)
    x.add_argument("--opt", action="append", help="chi=128 | th=1e-3 | level=2"); x.add_argument("--qubits", help="e.g. 0-2,7")
    x.add_argument("--max-cost", type=float); x.add_argument("--no-wait", action="store_true")
    x.add_argument("--force", action="store_true"); x.set_defaults(f=cmd_run)
    x = s.add_parser("build"); x.add_argument("circuit"); x.add_argument("tag")
    x.add_argument("--fill-missing", action="store_true", help="fill missing qubits with the mean so a partial run can still be submitted")
    x.set_defaults(f=cmd_build)
    s.add_parser("collect").set_defaults(f=cmd_collect)
    x = s.add_parser("score"); x.add_argument("a"); x.add_argument("b"); x.set_defaults(f=cmd_score)
    x = s.add_parser("blend"); x.add_argument("circuit"); x.add_argument("files", nargs="+")
    x.add_argument("--weights"); x.add_argument("--name"); x.set_defaults(f=cmd_blend)
    x = s.add_parser("validate"); x.add_argument("circuit"); x.add_argument("file"); x.set_defaults(f=cmd_validate)
    x = s.add_parser("candidates"); x.add_argument("circuit"); x.set_defaults(f=cmd_candidates)
    x = s.add_parser("rehearsal-score"); x.add_argument("circuit"); x.add_argument("file"); x.set_defaults(f=cmd_rehearsal_score)
    a = p.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    a.f(a)


if __name__ == "__main__":
    main()
