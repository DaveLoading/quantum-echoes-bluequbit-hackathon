"""Local simulators for OPEN circuits (samples, rehearsal). Hidden circuits can
only run on BlueQubit by ID, so these are for tuning and checking, not scoring."""
import itertools

import numpy as np
from qiskit import QuantumCircuit, qasm2, transpile
from qiskit.quantum_info import Pauli
from qiskit_aer import AerSimulator

EXACT_MAX_QUBITS = 26


def load_qasm(path):
    return qasm2.load(path, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def _expect_z(qc, probe, sim):
    c = qc.copy()
    c.save_expectation_value(Pauli("Z"), [probe])
    res = sim.run(transpile(c, sim, optimization_level=0)).result()
    return float(np.real(res.data()["expectation_value"]))


def exact_z(qc, probe):
    if qc.num_qubits > EXACT_MAX_QUBITS:
        raise ValueError(f"{qc.num_qubits} qubits is too many for exact (max {EXACT_MAX_QUBITS})")
    return _expect_z(qc, probe, AerSimulator(method="statevector"))


def mps_z(qc, probe, chi=64):
    sim = AerSimulator(method="matrix_product_state", matrix_product_state_max_bond_dimension=int(chi))
    return _expect_z(qc, probe, sim)


_P = [np.eye(2), np.array([[0, 1], [1, 0]]), np.array([[0, -1j], [1j, 0]]), np.diag([1, -1])]
_TAB_CACHE = {}


def _table(G, nq):
    key = (nq, G.tobytes())
    if key in _TAB_CACHE:
        return _TAB_CACHE[key]
    paulis = list(itertools.product(range(4), repeat=nq))
    mats = {p: (np.kron(_P[p[1]], _P[p[0]]) if nq == 2 else _P[p[0]]) for p in paulis}
    d = 2 ** nq
    tab = {}
    for p in paulis:
        M = G.conj().T @ mats[p] @ G
        tab[p] = [(Q, c.real) for Q in paulis if abs(c := np.trace(mats[Q] @ M) / d) > 1e-12]
    _TAB_CACHE[key] = tab
    return tab


def pauli_path_z(qc, probe, threshold=1e-3, max_terms=3_000_000):
    """Heisenberg-picture Pauli propagation of Z_probe, dropping |coeff| < threshold."""
    n = qc.num_qubits
    obs = [0] * n
    obs[probe] = 3
    terms = {tuple(obs): 1.0}
    for inst in reversed(qc.data):
        qs = [qc.find_bit(b).index for b in inst.qubits]
        tab = _table(inst.operation.to_matrix(), len(qs))
        new = {}
        for p, c in terms.items():
            key = tuple(p[q] for q in qs)
            if all(k == 0 for k in key):
                new[p] = new.get(p, 0.0) + c
                continue
            for Q, w in tab[key]:
                lst = list(p)
                for q, v in zip(qs, Q):
                    lst[q] = v
                t = tuple(lst)
                new[t] = new.get(t, 0.0) + c * w
        terms = {p: c for p, c in new.items() if abs(c) >= threshold}
        if len(terms) > max_terms:
            raise RuntimeError("too many Pauli terms: raise the threshold")
    return sum(c for p, c in terms.items() if all(v in (0, 3) for v in p))


def describe(qc):
    """Structure report for an open circuit: where the x / z gates sit, and
    whether the gate list after the first x mirrors the gates before it."""
    ops = [(inst.operation.name, [qc.find_bit(b).index for b in inst.qubits]) for inst in qc.data]
    counts = {}
    for name, _ in ops:
        counts[name] = counts.get(name, 0) + 1
    xs = [(i, q[0]) for i, (name, q) in enumerate(ops) if name == "x"]
    zs = [(i, q[0]) for i, (name, q) in enumerate(ops) if name == "z"]
    order = len(xs)
    return {
        "num_qubits": qc.num_qubits,
        "num_gates": len(ops),
        "count_ops": counts,
        "x_gates(position,qubit)": xs,
        "z_gates(position,qubit)": zs,
        "butterfly_qubits": sorted({q for _, q in xs}),
        "likely_otoc_order": order,
        "cz_depth_estimate": qc.depth(lambda inst: inst.operation.num_qubits == 2),
    }
