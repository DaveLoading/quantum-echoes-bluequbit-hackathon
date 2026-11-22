# Sample tuning (rehearsal run, 2026-11-20 Phoenix / 2026-11-21 06:10-06:16 UTC)

Mode: `BQ_MODE=rehearsal` (offline backend that simulates on this Mac). Costs and times below are rehearsal numbers, not live BlueQubit numbers.
Truth: `exact_local` for all three samples (S1 already existed; S2 took 1.0 s, S3 took 4.9 s). No stand-in truth was needed.
Total spent by the tuner: $0.098 of the $5.00 rehearsal budget.

## Results

Runtime is wall time for the whole setting, including the one-qubit timing run (`--qubits 0`) on S2 and S3.
s/qubit is wall time of the full run divided by the qubits in that run (the kit runs up to 8 at a time).

| sample | qubits | order | 2q depth | method | setting | corr² vs exact | cost | runtime | s/qubit | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | 12 | 1 | 12 | pauli-path | th=1e-2 | 0.9722 | $0.0048 | 0.8 s | 0.06 | passed first step, sweep stopped |
| S1 | 12 | 1 | 12 | mps.cpu | chi=16 | 0.9998 | $0.0048 | 0.7 s | 0.06 | passed first step, sweep stopped |
| S1 | 12 | 1 | 12 | cpu (statevector) | none | 1.0000 | $0.0000 | 0.9 s | 0.08 | extra data point |
| S1 | 12 | 1 | 12 | blend | mps chi=16 + pauli 1e-2 | 0.9929 | $0 | n/a | n/a | worse than mps alone |
| S2 | 16 | 2 | 24 | pauli-path | th=1e-2 | 0.8928 | $0.0064 | 2.4 s | 0.13 | |
| S2 | 16 | 2 | 24 | pauli-path | th=3e-3 | 0.9792 | $0.0117 | 7.2 s | 0.41 | passed, sweep stopped |
| S2 | 16 | 2 | 24 | mps.cpu | chi=16 | 0.9527 | $0.0064 | 1.5 s | 0.07 | passed (barely), sweep stopped |
| S2 | 16 | 2 | 24 | blend | pauli 3e-3 + mps chi=16 | 0.9740 | $0 | n/a | n/a | worse than pauli 3e-3 alone |
| S3 | 20 | 2 | 32 | pauli-path | th=1e-2 | 0.7836 | $0.0080 | 2.6 s | 0.11 | |
| S3 | 20 | 2 | 32 | pauli-path | th=3e-3 | 0.8678 | $0.0146 | 16.9 s | 0.82 | |
| S3 | 20 | 2 | 32 | pauli-path | th=1e-3 | 0.9370 | $0.0253 | 123.9 s | 6.13 | end of sweep, never reached 0.95; qubit 0 alone took 7.4 s |
| S3 | 20 | 2 | 32 | mps.cpu | chi=16 | 0.9174 | $0.0080 | 2.2 s | 0.09 | |
| S3 | 20 | 2 | 32 | mps.cpu | chi=64 | 0.9928 | $0.0080 | 37.5 s | 1.86 | passed, sweep stopped |
| S3 | 20 | 2 | 32 | cpu (statevector) | none | 1.0000 | $0.0000 | 5.2 s | 0.24 | extra data point requested by captain |
| S3 | 20 | 2 | 32 | blend | mps chi=64 + pauli 1e-3 | 0.9780 | $0 | n/a | n/a | worse than mps alone |

No setting was too slow locally (worst one-qubit timing was 7.4 s, pauli-path 1e-3 on S3). No "too many Pauli terms" failures.
Not tested: mps.cpu chi=256 (never needed), mps.cpu chi=64 on S2 (chi=16 had already passed).

## What the numbers say

- `cpu` (exact statevector on BlueQubit) matched exact perfectly on S1 (order 1) and S3 (order 2, 20 qubits) at $0 and 0.24 s per qubit. Free `estimate` on the graded circuits: G1 on cpu about $0.00 total, G2 on cpu about $0.07 total (rehearsal pricing). This is the strongest result of the sweep.
- pauli-path gets worse quickly as circuits get deeper and wider. At th=1e-2 it fell from 0.97 (S1) to 0.89 (S2) to 0.78 (S3). On S3 even th=1e-3 stayed below 0.95 and was the slowest and most expensive setting.
- mps.cpu holds up better on the deep order-2 circuit: chi=64 gave 0.993 on S3. Raising chi made it about 20x slower locally (0.09 to 1.86 s per qubit), but rehearsal cost stayed flat at $0.0004 per qubit. Live cost probably grows with runtime [Guessing]; the kit warns that `estimate` ignores chi and th.
- Blending hurt on all three samples (by 0.007, 0.005 and 0.015). With equal weights it pulls the better input toward the worse one.
- Warning for the judge: a blend always agrees well with its own inputs (0.99 on S2 and S3) even when it is less accurate than the better input. Blend-vs-input agreement is not evidence of convergence.
- Agreement between neighbouring settings of one method was a poor guide here. S3 mps chi=16 vs chi=64 agreed only 0.949 although chi=64 was 0.993 vs exact. S3 pauli 3e-3 vs 1e-3 agreed 0.961 although 1e-3 was only 0.937. No pair of non-exact settings reached the 0.98 "converged" mark.

## Recommendation by difficulty tier

| tier | sample like it | first choice | if cpu is not available | blending |
| --- | --- | --- | --- | --- |
| easy (about 12 q, shallow, order 1) | S1 | cpu | mps.cpu chi=16 (0.9998) | no |
| medium (about 16 q, depth about 24, order 2) | S2 | cpu [cpu not run on S2; inferred from S1 and S3] | pauli-path th=3e-3 (0.979), then mps.cpu chi=64 | no |
| hard (20 q and up, depth 32, order 2) | S3 | cpu | mps.cpu chi=64 (0.993), then chi=256 | no |

By OTOC order:
- Order 1: only S1 to go on (12 q, depth 12). Every method passed at its cheapest setting. The evidence for 20-qubit order-1 circuits is thin.
- Order 2: avoid pauli-path as the main method. mps.cpu needs chi=64 by 20 qubits at depth 32.

## Recommendation for the graded circuits

**G1 (300 points, 20 qubits, order 1, 2q depth 16)**
1. First: `run G1 --device cpu --max-cost 0.5`. Same width as S3, where cpu was exact. Rehearsal estimate is $0.00.
2. Tighter follow-up / cross-check: `run G1 --device mps.cpu --opt chi=64`. G1 is shallower than S3 and order 1, so chi=64 should be at least as good as on S3 [inference]. Use chi=256 only if chi=64 and cpu disagree below 0.98.
3. Blending: no. If cpu runs, upload cpu as is.

**G2 (600 points, 24 qubits, order 2, 2q depth 32)**
1. First: `run G2 --device cpu --max-cost 0.5`. Rehearsal estimate is about $0.07. 24 qubits is 16x the statevector of S3, so expect it to run slower.
2. Tighter follow-up / cross-check: `run G2 --device mps.cpu --opt chi=64`, then `chi=256`. S3 (same depth, 4 fewer qubits) needed chi=64 for 0.993; at 24 qubits chi=64 may fall short [Guessing], so chi=256 is the safer MPS answer if cpu fails.
3. Do not use pauli-path on G2: on the smaller S3 it never reached 0.95 and was the slowest and most expensive option.
4. Blending: no. It lowered corr² on every sample.

**Check before trusting this on live.** Rehearsal pricing and limits may not match live BlueQubit. Before the runner uses `cpu` on the live graded circuits, run the free `estimate G1 --device cpu` and `estimate G2 --device cpu` in live mode and confirm the price, and confirm that the live cpu device accepts 24 qubits [Guessing about live limits].
