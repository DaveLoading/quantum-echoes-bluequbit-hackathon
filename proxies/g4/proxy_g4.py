"""G4-style proxy study: small OTOC(2) echo circuits on snake-ordered grids,
exact vs MPS vs Pauli-path, scored with corr^2 like the site.

Local only. No BlueQubit calls. Uses kit/sim_local.py helpers unmodified.

Usage (from anywhere):
  python proxy_g4.py build  --rows 4 --cols 4 [--layers 10] [--seed 7]
  python proxy_g4.py exact  --tag g4x4
  python proxy_g4.py mps    --tag g4x4 --chis 4,8,16 [--workers 8]
  python proxy_g4.py pp     --tag g4x4 --ths 1e-2,3e-3 [--workers 8] [--probes 0,1]
  python proxy_g4.py cone   --tag g4x4
  python proxy_g4.py score  --tag g4x4
"""
import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", "..", "kit"))
sys.path.insert(0, KIT)

from qiskit import QuantumCircuit, qasm2  # noqa: E402
from qiskit.circuit.library import CZGate, UnitaryGate  # noqa: E402
from qiskit.synthesis import TwoQubitBasisDecomposer  # noqa: E402
from qiskit.transpiler import PassManager  # noqa: E402
from qiskit.transpiler.passes import Optimize1qGatesDecomposition  # noqa: E402

import sim_local  # noqa: E402


# ---------------------------------------------------------------- geometry
def snake_index(r, c, cols):
    return r * cols + (c if r % 2 == 0 else cols - 1 - c)


def grid_patterns(rows, cols):
    """Google-style ABCD coupler patterns on a square grid, as qubit pairs.
    A: horizontal, left col even. B: horizontal, left col odd.
    C: vertical, top row even.    D: vertical, top row odd."""
    pats = {"A": [], "B": [], "C": [], "D": []}
    for r in range(rows):
        for c in range(cols - 1):
            pats["A" if c % 2 == 0 else "B"].append((snake_index(r, c, cols), snake_index(r, c + 1, cols)))
    for r in range(rows - 1):
        for c in range(cols):
            pats["C" if r % 2 == 0 else "D"].append((snake_index(r, c, cols), snake_index(r + 1, c, cols)))
    return pats


def fsim(theta, phi):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[1, 0, 0, 0],
                     [0, c, -1j * s, 0],
                     [0, -1j * s, c, 0],
                     [0, 0, 0, np.exp(-1j * phi)]], dtype=complex)


SQ = {
    "sx": np.array([[1, -1j], [-1j, 1]]) / np.sqrt(2),
    "sy": np.array([[1, -1], [1, 1]]) / np.sqrt(2),
    # sqrt(W), W = (X+Y)/sqrt2
    "sw": np.array([[1, -np.sqrt(1j)], [np.sqrt(-1j), 1]]) / np.sqrt(2),
}


_DEC = TwoQubitBasisDecomposer(CZGate(), euler_basis="U3")


def build_U(n, rows, cols, layers, rng, theta=np.pi / 2, phi=np.pi / 6, jitter=0.03):
    pats = grid_patterns(rows, cols)
    order = "ABCD"
    U = QuantumCircuit(n)
    last = [None] * n
    for L in range(layers):
        for q in range(n):
            choices = [k for k in SQ if k != last[q]]
            k = choices[rng.integers(len(choices))]
            last[q] = k
            U.append(UnitaryGate(SQ[k]), [q])
        for (a, b) in pats[order[L % 4]]:
            th = theta + jitter * rng.standard_normal()
            ph = phi + jitter * rng.standard_normal()
            sub = _DEC(fsim(th, ph))
            U.compose(sub, qubits=[a, b], inplace=True)
    # 1q gates to u3, merge runs of 1q gates
    pm = PassManager([Optimize1qGatesDecomposition(basis=["u3"])])
    return pm.run(U)


def echo_circuit(U, Ud, butterfly, probe):
    n = U.num_qubits
    qc = QuantumCircuit(n)
    qc.compose(U, inplace=True)
    qc.x(butterfly)
    qc.compose(Ud, inplace=True)
    qc.z(probe)
    qc.compose(U, inplace=True)
    qc.x(butterfly)
    qc.compose(Ud, inplace=True)
    return qc


def tagdir(tag):
    return os.path.join(HERE, tag)


def load_meta(tag):
    with open(os.path.join(tagdir(tag), "meta.json")) as f:
        return json.load(f)


def load_circ(tag, q):
    return sim_local.load_qasm(os.path.join(tagdir(tag), "qasm", f"{tag}_{q}.qasm"))


# ---------------------------------------------------------------- commands
def cmd_build(a):
    rows, cols = a.rows, a.cols
    n = rows * cols
    tag = a.tag or f"g{rows}x{cols}" + (f"_L{a.layers}" if a.layers != 10 else "") + (f"_s{a.seed}" if a.seed != 7 else "")
    rng = np.random.default_rng(a.seed)
    if a.butterfly is not None:
        b = a.butterfly
    else:  # interior site nearest the centre, biased like G4 (row 3 col 2 of 6x6)
        br, bc = (rows * 3) // 6, (cols * 2) // 6
        br = min(max(br, 1), rows - 2)
        bc = min(max(bc, 1), cols - 2)
        b = snake_index(br, bc, cols)
    U = build_U(n, rows, cols, a.layers, rng)
    Ud = U.inverse()
    # inverse of u3 is u3 already; keep basis u3+cz
    os.makedirs(os.path.join(tagdir(tag), "qasm"), exist_ok=True)
    for q in range(n):
        qc = echo_circuit(U, Ud, b, q)
        with open(os.path.join(tagdir(tag), "qasm", f"{tag}_{q}.qasm"), "w") as f:
            f.write(qasm2.dumps(qc))
    qc = echo_circuit(U, Ud, b, 0)
    ops = dict(qc.count_ops())
    meta = {"tag": tag, "rows": rows, "cols": cols, "n": n, "layers": a.layers, "seed": a.seed,
            "butterfly": b, "count_ops": {k: int(v) for k, v in ops.items()},
            "cz_depth": qc.depth(lambda i: i.operation.num_qubits == 2)}
    with open(os.path.join(tagdir(tag), "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta))


def write_csv(path, vals, secs):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qubit", "value", "seconds"])
        for q in sorted(vals):
            w.writerow([q, f"{vals[q]:.10g}", f"{secs[q]:.3f}"])


def read_csv(path):
    vals, secs = {}, {}
    with open(path) as f:
        for row in csv.DictReader(f):
            vals[int(row["qubit"])] = float(row["value"])
            secs[int(row["qubit"])] = float(row["seconds"])
    return vals, secs


def cmd_exact(a):
    meta = load_meta(a.tag)
    n = meta["n"]
    vals, secs = {}, {}
    for q in range(n):
        qc = load_circ(a.tag, q)
        t = time.time()
        vals[q] = sim_local.exact_z(qc, q)
        secs[q] = time.time() - t
        print(f"exact q{q} {vals[q]:+.5f} {secs[q]:.2f}s", flush=True)
    write_csv(os.path.join(tagdir(a.tag), "exact.csv"), vals, secs)


def _mps_job(args):
    tag, q, chi = args
    qc = load_circ(tag, q)
    t = time.time()
    v = sim_local.mps_z(qc, q, chi=chi)
    return q, v, time.time() - t


def _pp_job(args):
    tag, q, th = args
    qc = load_circ(tag, q)
    t = time.time()
    try:
        v = sim_local.pauli_path_z(qc, q, threshold=th)
    except RuntimeError:
        v = float("nan")
    return q, v, time.time() - t


def _run_pool(job, items, workers):
    out = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for q, v, s in ex.map(job, items):
            out[q] = (v, s)
    return out


def cmd_mps(a):
    meta = load_meta(a.tag)
    n = meta["n"]
    probes = [int(x) for x in a.probes.split(",")] if a.probes else list(range(n))
    for chi in [int(x) for x in a.chis.split(",")]:
        t0 = time.time()
        res = _run_pool(_mps_job, [(a.tag, q, chi) for q in probes], a.workers)
        vals = {q: v for q, (v, s) in res.items()}
        secs = {q: s for q, (v, s) in res.items()}
        write_csv(os.path.join(tagdir(a.tag), f"mps_chi{chi}.csv"), vals, secs)
        print(f"mps chi={chi} done in {time.time()-t0:.1f}s wall, {np.mean(list(secs.values())):.2f}s/qubit", flush=True)


def cmd_pp(a):
    meta = load_meta(a.tag)
    n = meta["n"]
    probes = [int(x) for x in a.probes.split(",")] if a.probes else list(range(n))
    for th in [float(x) for x in a.ths.split(",")]:
        t0 = time.time()
        res = _run_pool(_pp_job, [(a.tag, q, th) for q in probes], a.workers)
        vals = {q: v for q, (v, s) in res.items()}
        secs = {q: s for q, (v, s) in res.items()}
        name = f"pp_th{th:g}" + ("_partial" if a.probes else "")
        write_csv(os.path.join(tagdir(a.tag), f"{name}.csv"), vals, secs)
        print(f"pp th={th:g} done in {time.time()-t0:.1f}s wall, {np.mean(list(secs.values())):.2f}s/qubit", flush=True)


def light_cone(qc, start, forward=True):
    """Qubits that can be touched by an operator starting on `start`, sweeping the
    two-qubit gates of qc forward (or backward)."""
    cone = {start}
    data = qc.data if forward else list(reversed(qc.data))
    for inst in data:
        if inst.operation.num_qubits == 2:
            qs = [qc.find_bit(b).index for b in inst.qubits]
            if cone.intersection(qs):
                cone.update(qs)
    return cone


def cmd_cone(a):
    meta = load_meta(a.tag)
    rows, cols, b = meta["rows"], meta["cols"], meta["butterfly"]
    qc = load_circ(a.tag, 0)
    # U is the first quarter of the circuit (up to the first x)
    U = QuantumCircuit(qc.num_qubits)
    for inst in qc.data:
        if inst.operation.name == "x":
            break
        U.append(inst)
    # support of U^dag X_b U: sweep U backward from the x (last layer first)
    cone = light_cone(U, b, forward=False)
    grid = []
    for r in range(rows):
        line = []
        for c in range(cols):
            q = snake_index(r, c, cols)
            line.append(("B" if q == b else ("#" if q in cone else ".")) + f"{q:>3}")
        grid.append(" ".join(line))
    out = {"butterfly": b, "cone_size": len(cone), "n": qc.num_qubits,
           "outside": sorted(set(range(qc.num_qubits)) - cone), "grid": grid}
    print(json.dumps(out, indent=1))
    with open(os.path.join(tagdir(a.tag), "cone.json"), "w") as f:
        json.dump(out, f, indent=1)


def corr2(x, y):
    x, y = np.asarray(x), np.asarray(y)
    if np.std(x) == 0 or np.std(y) == 0 or np.any(~np.isfinite(x)):
        return float("nan")
    r = np.corrcoef(x, y)[0, 1]
    return float(max(r, 0.0) ** 2)


def cmd_score(a):
    d = tagdir(a.tag)
    ex, exs = read_csv(os.path.join(d, "exact.csv"))
    qs = sorted(ex)
    xe = [ex[q] for q in qs]
    rows = [{"method": "exact", "corr2": 1.0, "floored": 1.0, "sec_per_qubit": np.mean(list(exs.values())),
             "max_abs_err": 0.0, "n": len(qs)}]
    files = sorted(f for f in os.listdir(d) if f.endswith(".csv") and f not in ("exact.csv", "scores.csv"))
    for f in files:
        v, s = read_csv(os.path.join(d, f))
        common = [q for q in qs if q in v]
        if len(common) < 3:
            continue
        c2 = corr2([v[q] for q in common], [ex[q] for q in common])
        err = max(abs(v[q] - ex[q]) for q in common)
        rows.append({"method": f[:-4], "corr2": c2, "floored": (np.floor(c2 * 10 + 1e-9) / 10 if np.isfinite(c2) else float("nan")),
                     "sec_per_qubit": float(np.mean([s[q] for q in common])), "max_abs_err": float(err), "n": len(common)})
    with open(os.path.join(d, "scores.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"exact: mean {np.mean(xe):+.4f} std {np.std(xe):.4f} min {min(xe):+.4f} max {max(xe):+.4f}")
    for r in rows:
        print(f"{r['method']:<24} corr2={r['corr2']:.4f} floor={r['floored']} s/q={r['sec_per_qubit']:.2f} maxerr={r['max_abs_err']:.4f} n={r['n']}")


def cmd_summary(a):
    """Score every proxy that has exact.csv and write one combined summary.csv."""
    out = []
    for tag in sorted(os.listdir(HERE)):
        d = tagdir(tag)
        if not os.path.isfile(os.path.join(d, "exact.csv")):
            continue
        print(f"== {tag}")
        cmd_score(argparse.Namespace(tag=tag))
        meta = load_meta(tag)
        ex, _ = read_csv(os.path.join(d, "exact.csv"))
        xs = np.array(list(ex.values()))
        with open(os.path.join(d, "scores.csv")) as f:
            for r in csv.DictReader(f):
                out.append({"proxy": tag, "n": meta["n"], "layers_per_U": meta["layers"],
                            "exact_std": f"{xs.std():.4f}", "exact_max_abs": f"{np.abs(xs).max():.4f}", **r})
    with open(os.path.join(HERE, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--rows", type=int, required=True)
    b.add_argument("--cols", type=int, required=True)
    b.add_argument("--layers", type=int, default=10)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--butterfly", type=int, default=None)
    b.add_argument("--tag", default=None)
    sub.add_parser("summary")
    for name in ("exact", "mps", "pp", "cone", "score"):
        s = sub.add_parser(name)
        s.add_argument("--tag", required=True)
        s.add_argument("--workers", type=int, default=8)
        s.add_argument("--probes", default=None)
        if name == "mps":
            s.add_argument("--chis", default="4,8,16,32,64,128,256")
        if name == "pp":
            s.add_argument("--ths", default="1e-2,3e-3,1e-3")
    a = p.parse_args()
    {"build": cmd_build, "exact": cmd_exact, "mps": cmd_mps, "pp": cmd_pp, "cone": cmd_cone, "score": cmd_score,
     "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    main()
