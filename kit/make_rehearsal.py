"""Build the offline rehearsal set: open samples (zipped QASM, like the real
event) and 'hidden' graded circuits whose exact answers only the fake scorer
reads. Same gate set as the Duke round: u3, cz, x, z.

  python kit/make_rehearsal.py      (about 3 minutes; output in kit/rehearsal/)
"""
import json
import os
import shutil
import zipfile

import numpy as np
from qiskit import QuantumCircuit, qasm2, transpile

from sim_local import exact_z

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "rehearsal")
PREFIX = "Rehearsal"

# name: (rows, cols, cycles, butterfly, order, seed, open_sample)
SPEC = {
    "S1": (3, 4, 6, 5, 1, 11, True),
    "S2": (4, 4, 6, 6, 2, 12, True),
    "S3": (4, 5, 8, 7, 2, 13, True),
    "G1": (4, 5, 8, 12, 1, 21, False),
    "G2": (4, 6, 8, 9, 2, 22, False),
}


def grid_layers(rows, cols):
    idx = lambda r, c: r * cols + c
    return [
        [(idx(r, c), idx(r, c + 1)) for r in range(rows) for c in range(0, cols - 1, 2)],
        [(idx(r, c), idx(r + 1, c)) for r in range(0, rows - 1, 2) for c in range(cols)],
        [(idx(r, c), idx(r, c + 1)) for r in range(rows) for c in range(1, cols - 1, 2)],
        [(idx(r, c), idx(r + 1, c)) for r in range(1, rows - 1, 2) for c in range(cols)],
    ]


def random_U(rows, cols, cycles, seed):
    rng = np.random.default_rng(seed)
    n = rows * cols
    U = QuantumCircuit(n)
    layers = grid_layers(rows, cols)
    for t in range(cycles):
        for q in range(n):
            U.u(*rng.uniform(0, 2 * np.pi, 3), q)
        for a, b in layers[t % 4]:
            U.cz(a, b)
    return U


def echo(U, butterfly, probe, order):
    qc = QuantumCircuit(U.num_qubits, name="echo")
    Ud = U.inverse()
    for k in range(order):
        if k:
            qc.z(probe)
        qc.compose(U, inplace=True)
        qc.x(butterfly)
        qc.compose(Ud, inplace=True)
    return transpile(qc, basis_gates=["u3", "cz", "x", "z"], optimization_level=0)


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "samples"))
    hidden = os.path.join(OUT, ".hidden")
    for name, (rows, cols, cycles, b, order, seed, is_open) in SPEC.items():
        U = random_U(rows, cols, cycles, seed)
        n = rows * cols
        cdir = os.path.join(hidden, name)
        os.makedirs(cdir)
        truth = []
        for q in range(n):
            qc = echo(U, b, q, order)
            with open(os.path.join(cdir, f"{PREFIX}_{name}_{q}.qasm"), "w") as f:
                f.write(qasm2.dumps(qc))
            truth.append(exact_z(qc, q))
        with open(os.path.join(cdir, "truth.json"), "w") as f:
            json.dump(truth, f)
        if is_open:
            with zipfile.ZipFile(os.path.join(OUT, "samples", f"{PREFIX}_{name}.zip"), "w") as z:
                for q in range(n):
                    fn = f"{PREFIX}_{name}_{q}.qasm"
                    z.write(os.path.join(cdir, fn), fn)
        print(f"{name}: {n} qubits, order {order}, {'open sample' if is_open else 'hidden'}")


if __name__ == "__main__":
    main()
