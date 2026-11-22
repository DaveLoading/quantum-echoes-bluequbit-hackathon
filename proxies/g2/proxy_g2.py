"""G2 proxy: 1D Haar brickwork chain with weak bonds, order-2 OTOC echo.
Local only. No BlueQubit calls. Run with BLUEQUBIT_API_TOKEN= prefix.

  python proxy_g2.py build
  python proxy_g2.py run <variant> <method> <setting> [probe ...]
      variant: PA, PB (proxies) or S2
      method: exact | mps | pp (kit's Python engine) | ppf (vectorized clone of the same algorithm)
  python proxy_g2.py score
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
import csv, glob, json, sys, time
from multiprocessing import Pool

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.abspath(os.path.join(HERE, "..", "..", "kit"))
sys.path.insert(0, KIT)
import sim_local  # noqa: E402  (exact_z, mps_z, pauli_path_z; not modified)
from qiskit import QuantumCircuit, qasm2  # noqa: E402
from qiskit.circuit.library import CZGate  # noqa: E402
from qiskit.synthesis import TwoQubitBasisDecomposer  # noqa: E402
from scipy.linalg import expm  # noqa: E402
from scipy.stats import unitary_group  # noqa: E402

N = 20
LAYERS = 20
BUTTERFLIES = [2, 15]           # G2: 2 and 31 of 36 (2 from the left, 5 from the right)
WEAK_BONDS = [5, 11, 17]        # bond i couples qubits i and i+1; all odd = same parity
VARIANTS = {"PA": 0.10, "PB": 0.30}   # eps of the weak gate exp(-i eps H)
SEED = 2026

QDIR = os.path.join(HERE, "qasm")
CDIR = os.path.join(HERE, "csv")
LOG = os.path.join(HERE, "results.jsonl")
S2_QASM = os.path.abspath(os.path.join(HERE, "..", "qasm", "S2"))
S2_EXACT = os.path.abspath(os.path.join(HERE, "..", "csv", "S2__exact_local.csv"))

_PX = np.array([[0, 1], [1, 0]], complex)
_PY = np.array([[0, -1j], [1j, 0]], complex)
_PZ = np.diag([1, -1]).astype(complex)
_I2 = np.eye(2, dtype=complex)


def weak_gate(rng, eps):
    """exp(-i eps H), H a random pure two-body Hermitian sum c_ab s_a s_b, sum c^2 = 1."""
    c = rng.normal(size=(3, 3))
    c /= np.linalg.norm(c)
    H = sum(c[a, b] * np.kron(P, Q) for a, P in enumerate([_PX, _PY, _PZ])
            for b, Q in enumerate([_PX, _PY, _PZ]))
    return expm(-1j * eps * H)


def build_U(eps):
    rng = np.random.default_rng(SEED)        # same Haar gates for every variant
    rng_w = np.random.default_rng(SEED + 1)  # same weak generators for every variant
    dec = TwoQubitBasisDecomposer(CZGate(), euler_basis="U3")
    U = QuantumCircuit(N)
    for layer in range(LAYERS):
        for i in range(layer % 2, N - 1, 2):
            G = unitary_group.rvs(4, random_state=rng)
            if i in WEAK_BONDS:
                G = weak_gate(rng_w, eps)
            U.compose(dec(G), qubits=[i, i + 1], inplace=True)
    return U


def build():
    meta = {"N": N, "layers_per_U": LAYERS, "butterflies": BUTTERFLIES, "weak_bonds": WEAK_BONDS,
            "variants_eps": VARIANTS, "seed": SEED,
            "weak_gate": "exp(-i eps H), H = sum_ab c_ab sigma_a x sigma_b (a,b in x,y,z), c Gaussian, |c|=1",
            "echo": "U, x on butterflies, U^dag, z on probe, U, x on butterflies, U^dag"}
    for v, eps in VARIANTS.items():
        U = build_U(eps)
        Ud = U.inverse()
        os.makedirs(os.path.join(QDIR, v), exist_ok=True)
        for q in range(N):
            qc = QuantumCircuit(N)
            qc.compose(U, inplace=True)
            for b in BUTTERFLIES:
                qc.x(b)
            qc.compose(Ud, inplace=True)
            qc.z(q)
            qc.compose(U, inplace=True)
            for b in BUTTERFLIES:
                qc.x(b)
            qc.compose(Ud, inplace=True)
            with open(os.path.join(QDIR, v, f"{v}_{q}.qasm"), "w") as f:
                f.write(qasm2.dumps(qc))
        meta[v] = sim_local.describe(sim_local.load_qasm(os.path.join(QDIR, v, f"{v}_0.qasm")))
    with open(os.path.join(HERE, "proxy_meta.json"), "w") as f:
        json.dump(meta, f, indent=1, default=str)
    print(json.dumps({v: meta[v]["count_ops"] for v in VARIANTS}), "cz depth",
          {v: meta[v]["cz_depth_estimate"] for v in VARIANTS})


# ---------- vectorized Pauli propagation (same algorithm as sim_local.pauli_path_z) ----------
# key bit q = x_q, bit q+32 = z_q. local code c = x | z<<1 : 0=I 1=X 2=Z 3=Y
_CODE_TO_MAT = [_I2, _PX, _PZ, _PY]


def _ptm(G, nq):
    d = 2 ** nq
    mats = []
    for k in range(4 ** nq):
        if nq == 1:
            mats.append(_CODE_TO_MAT[k])
        else:
            mats.append(np.kron(_CODE_TO_MAT[k >> 2], _CODE_TO_MAT[k & 3]))
    R = np.zeros((d * d, d * d))
    for a in range(d * d):
        M = G.conj().T @ mats[a] @ G
        for b in range(d * d):
            R[a, b] = np.real(np.trace(mats[b] @ M)) / d
    R[np.abs(R) < 1e-12] = 0.0
    return R


def _local_code(keys, qs):
    k = np.zeros(keys.shape, dtype=np.int64)
    for j, q in enumerate(qs):
        x = ((keys >> np.uint64(q)) & np.uint64(1)).astype(np.int64)
        z = ((keys >> np.uint64(q + 32)) & np.uint64(1)).astype(np.int64)
        k |= (x | (z << 1)) << (2 * j)
    return k


def _code_bits(code, qs):
    bits = 0
    for j, q in enumerate(qs):
        c = (code >> (2 * j)) & 3
        bits |= (c & 1) << q
        bits |= ((c >> 1) & 1) << (q + 32)
    return np.uint64(bits)


def pp_fast(qc, probe, threshold, max_terms=20_000_000):
    n = qc.num_qubits
    assert n <= 32
    keys = np.array([1 << (probe + 32)], dtype=np.uint64)
    coef = np.array([1.0])
    ops = [(inst.operation.to_matrix(), [qc.find_bit(b).index for b in inst.qubits]) for inst in qc.data]
    peak = 1
    cache = {}
    for G, qs in reversed(ops):
        nq = len(qs)
        ck = (nq, G.tobytes())
        if ck not in cache:
            cache[ck] = _ptm(G, nq)
        R = cache[ck]
        lmask = _code_bits(4 ** nq - 1, qs)
        loc = _local_code(keys, qs)
        nt = loc != 0
        if not nt.any():
            continue
        keep_k, keep_c = keys[~nt], coef[~nt]
        kt, ct, lt = keys[nt] & ~lmask, coef[nt], loc[nt]
        perm = bool(np.all((R[1:, 1:] != 0).sum(axis=1) == 1))
        outk, outc = [keep_k], [keep_c]
        W = R[lt]  # rows for each term
        for j in range(1, 4 ** nq):
            w = W[:, j]
            sel = w != 0
            if sel.any():
                outk.append(kt[sel] | _code_bits(j, qs))
                outc.append(ct[sel] * w[sel])
        if perm:
            keys = np.concatenate(outk)
            coef = np.concatenate(outc)
        else:
            nk = np.concatenate(outk[1:])
            nc = np.concatenate(outc[1:])
            codes, uniq = pd.factorize(nk)
            sc = np.bincount(codes, weights=nc)
            keys = np.concatenate([keep_k, uniq.astype(np.uint64)])
            coef = np.concatenate([keep_c, sc])
        m = np.abs(coef) >= threshold
        keys, coef = keys[m], coef[m]
        peak = max(peak, len(keys))
        if len(keys) > max_terms:
            raise RuntimeError("too many Pauli terms")
    xmask = np.uint64((1 << n) - 1)
    return float(coef[(keys & xmask) == 0].sum()), peak


# ---------- runners ----------

def qasm_path(variant, q):
    if variant == "S2":
        return os.path.join(S2_QASM, f"MQW_S2_{q}.qasm")
    return os.path.join(QDIR, variant, f"{variant}_{q}.qasm")


def cid(variant, q):
    return f"MQW_S2_{q}" if variant == "S2" else f"{variant}_{q}"


def nqubits(variant):
    return 16 if variant == "S2" else N


def one(args):
    variant, method, setting, q = args
    qc = sim_local.load_qasm(qasm_path(variant, q))
    t = time.time()
    extra = {}
    if method == "exact":
        v = sim_local.exact_z(qc, q)
    elif method == "mps":
        v = sim_local.mps_z(qc, q, chi=int(setting))
    elif method == "pp":
        v = sim_local.pauli_path_z(qc, q, threshold=float(setting))
    elif method == "ppf":
        v, peak = pp_fast(qc, q, float(setting))
        extra["peak_terms"] = int(peak)
    else:
        raise ValueError(method)
    dt = time.time() - t
    rec = {"variant": variant, "method": method, "setting": setting, "q": q, "value": v, "sec": round(dt, 2), **extra}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def run(variant, method, setting, probes=None, workers=10):
    probes = probes if probes else list(range(nqubits(variant)))
    with Pool(min(workers, len(probes))) as pool:
        recs = pool.map(one, [(variant, method, setting, q) for q in probes], chunksize=1)
    recs.sort(key=lambda r: r["q"])
    os.makedirs(CDIR, exist_ok=True)
    if len(probes) == nqubits(variant):
        path = os.path.join(CDIR, f"{variant}__{method}_{setting}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["circuit_id", "value"])
            for r in recs:
                w.writerow([cid(variant, r["q"]), r["value"]])
        print("wrote", path)
    for r in recs:
        print(r)
    print("mean sec per qubit", round(np.mean([r["sec"] for r in recs]), 2))


def load_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return np.array([float(r["value"]) for r in rows])


def corr2(a, b):
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0  # constant vector: Pearson undefined, scores 0
    r = np.corrcoef(a, b)[0, 1]
    return max(r, 0.0) ** 2


def crude():
    """Guesses that need no simulation: step on protected segments, distance to butterflies."""
    q = np.arange(N)
    seg = np.searchsorted(np.array(WEAK_BONDS) + 0.5, q)  # segment index per qubit
    bseg = {int(seg[b]) for b in BUTTERFLIES}
    step = np.array([0.0 if s in bseg else 1.0 for s in seg])
    dist = np.min(np.abs(q[:, None] - np.array(BUTTERFLIES)[None, :]), axis=1).astype(float)
    for v in VARIANTS:
        ex = load_csv(os.path.join(CDIR, f"{v}__exact_local.csv"))
        print(v, "step(known bonds) corr2=%.3f" % corr2(step, ex), " distance-to-butterfly corr2=%.3f" % corr2(dist, ex))
    ex = load_csv(S2_EXACT)
    print("S2 distance-to-butterfly corr2=%.3f" % corr2(np.arange(16.0), ex))


def score():
    secs = {}
    if os.path.exists(LOG):
        for line in open(LOG):
            r = json.loads(line)
            secs.setdefault((r["variant"], r["method"], str(r["setting"])), []).append(r["sec"])
    out = []
    for path in sorted(glob.glob(os.path.join(CDIR, "*.csv"))):
        name = os.path.basename(path)[:-4]
        variant, ms = name.split("__")
        method, setting = ms.split("_", 1)
        if method == "exact":
            continue
        ex = load_csv(S2_EXACT if variant == "S2" else os.path.join(CDIR, f"{variant}__exact_local.csv"))
        v = load_csv(path)
        s = secs.get((variant, method, setting), [])
        out.append((variant, method, setting, corr2(v, ex), np.mean(s) if s else float("nan")))
    for o in out:
        print(f"{o[0]:4s} {o[1]:6s} {o[2]:8s} corr2={o[3]:.3f} floor={np.floor(o[3]*10)/10:.1f} sec/q={o[4]:.1f}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "run":
        variant, method, setting = sys.argv[2:5]
        probes = [int(x) for x in sys.argv[5:]]
        run(variant, method, setting, probes)
    elif cmd == "score":
        score()
    elif cmd == "crude":
        crude()
