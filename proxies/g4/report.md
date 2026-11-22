# G4 proxy: which method keeps the pattern (2026-11-21, local only, $0)

Saved by the captain from the proxy agent's reply (subagents can't write report files).

- Build: Google-style snake-ordered grids. Each cycle is a random sqrt(X), sqrt(Y) or sqrt(W) per qubit, then fSim (theta about pi/2, phi about pi/6) on one of 4 coupler patterns cycling A B C D. 3 CZ per fSim, so CZ depth 120 like G4. Butterfly at row 2, col 1. Proxies: 4x4 and 4x5 at 10 and 8 layers, and 5x5 at 10 layers.
- Value size: 5x5 with 10 layers best matches G4's ±0.1. With A B C D order, butterfly 21 reaches all 38 G4 qubits by 8 layers, so no qubit is trivially +1.

| Proxy | chi 16 | 32 | 64 | 128 |
| --- | --- | --- | --- | --- |
| 16q L10 | 0.08 | 0.03 | 0.06 | 0.13 |
| 16q L8 | 0.02 | 0.16 | 0.54 | 0.88 |
| 20q L10 | 0.07 | 0.47 | 0.14 | not run |
| 20q L8 | not run | 0.24 | 0.29 | 0.46 |
| 25q L10 | 0.39 | 0.46 | 0.37 | not run |

- Pauli-path (local engine, 16 qubits): all zeros at every threshold on 10 layers; 0.31 (3e-3) and 0.50 (1e-3) on 8 layers.
- Recommendation: MPS at the largest affordable chi. Expected corr² about 0.2 to 0.5 [Guessing]; clearing 0.9 looks very unlikely. Agreement between two chi values does not mean either is right (20q L8: chi 64 vs 128 agree r = 0.95, yet chi 128 scores 0.46).
- Files: proxy_g4.py, summary.csv, one folder per proxy (qasm, exact.csv, mps_chi*.csv, pp_th*.csv, scores.csv), cone.json, run logs.
