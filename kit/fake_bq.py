"""Offline stand-in for the BlueQubit SDK, used when BQ_MODE=rehearsal.
Same calls the kit uses (get_circuit_info, estimate, run, wait, get), local
simulation underneath, and a made-up cost so budget rules get exercised.

Agents must NOT read kit/rehearsal/.hidden/: it holds the answer key.
"""
import json
import os
import uuid
from dataclasses import dataclass

from sim_local import exact_z, load_qasm, mps_z, pauli_path_z

HIDDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rehearsal", ".hidden")
_JOBS = {}
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rehearsal", ".jobs.json")


def _path(cid):
    _, name, q = cid.rsplit("_", 2)
    p = os.path.join(HIDDEN, name, f"{cid}.qasm")
    if not os.path.exists(p):
        raise ValueError(f"unknown circuit id {cid}")
    return p, int(q)


def hidden_truth(name):
    with open(os.path.join(HIDDEN, name, "truth.json")) as f:
        return json.load(f)


@dataclass
class Info:
    circuit_id: str
    num_qubits: int
    count_ops: dict
    depth_1q: int
    depth_2q: int


@dataclass
class Job:
    job_id: str
    run_status: str
    expectation_value: float | None
    cost: float | None
    error_message: str | None = None


@dataclass
class Estimate:
    estimated_cost: float
    estimated_runtime: int
    warning_message: str | None = None
    error_message: str | None = None


def _load_state():
    if os.path.exists(_STATE):
        with open(_STATE) as f:
            for k, v in json.load(f).items():
                _JOBS.setdefault(k, Job(**v))


def _save_state():
    with open(_STATE, "w") as f:
        json.dump({k: v.__dict__ for k, v in _JOBS.items()}, f)


class FakeBQ:
    def __init__(self):
        _load_state()

    def get_circuit_info(self, cid):
        p, _ = _path(cid)
        qc = load_qasm(p)
        return Info(cid, qc.num_qubits, dict(qc.count_ops()),
                    qc.depth(lambda i: i.operation.num_qubits == 1),
                    qc.depth(lambda i: i.operation.num_qubits == 2))

    def estimate(self, cid, device="cpu"):
        p, _ = _path(cid)
        n = load_qasm(p).num_qubits
        return Estimate(self._cost(device, n, {}) / n, 500)

    @staticmethod
    def _cost(device, n, opts):
        if device.startswith("mps"):
            return 0.0004 * n * max(1, opts.get("mps_bond_dimension", 256) / 64)
        if device.startswith("pauli-path"):
            th = opts.get("pauli_path_truncation_threshold") or 1e-5
            return 0.0004 * n * min(20, (1e-2 / th) ** 0.5)
        return 0.0 if n <= 20 else 0.003 * n

    def run(self, cid, device="cpu", pauli_sum=None, options=None, asynchronous=False, **_):
        opts = options or {}
        p, q_from_id = _path(cid)
        qc = load_qasm(p)
        n = qc.num_qubits
        z = pauli_sum[0][0]
        if len(z) != n or z.count("Z") != 1:
            raise ValueError("pauli_sum must be a single Z string of length num_qubits")
        q = n - 1 - z.index("Z")          # qubit 0 is the rightmost letter
        jid = uuid.uuid4().hex[:12]
        try:
            if device in ("cpu", "gpu"):
                v = exact_z(qc, q)
            elif device.startswith("mps"):
                v = mps_z(qc, q, opts.get("mps_bond_dimension", 256))
            elif device.startswith("pauli-path"):
                th = opts.get("pauli_path_truncation_threshold")
                if th is None and n > 13:
                    raise ValueError("pauli_path_truncation_threshold required above 13 qubits")
                v = pauli_path_z(qc, q, th or 0.0)
            else:
                raise ValueError(f"device {device} not in rehearsal")
            job = Job(jid, "COMPLETED", float(v), self._cost(device, n, opts) / n)
        except (RuntimeError, ValueError) as ex:
            job = Job(jid, "FAILED", None, 0.0, str(ex))
        _JOBS[jid] = job
        _save_state()
        return job

    def wait(self, jobs, timeout=None):
        return [self.get(j.job_id) for j in jobs] if isinstance(jobs, list) else self.get(jobs.job_id)

    def get(self, job_id):
        return _JOBS[job_id]
