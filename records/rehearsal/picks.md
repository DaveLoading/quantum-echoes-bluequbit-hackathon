# Upload picks (judge, rehearsal, updated 2026-11-20 23:21 Phoenix)

One section per circuit. Upload counts come from `work/submissions.md`. In rehearsal, a `rehearsal-score` counts as an upload.

## S1 (open sample, 12 qubits, order 1)

- File: <kit>/work/csv/S1__exact_local.csv (already uploaded)
- Uploads used: 1 of 10. Score 1.0000. Done, no second upload.
- Why: 1.0000 is the highest score possible. `S1__cpu.csv` is byte-identical to it. The others score lower against exact: mps chi=16 0.9998, the blend 0.9929, pauli-path 1e-2 0.9722. None of them can raise the score.
- Method note: Exact statevector simulation of the provided QASM, run locally; one ⟨Z_q⟩ per qubit, no truncation.

## S2 (open sample, 16 qubits, order 2)

- File: <kit>/work/csv/S2__exact_local.csv
- Uploads used: 1 of 10 (this pick, rehearsal score 1.0000). Done, no second upload.
- Why: The exact answer is available for an open sample. The best approximate run was pauli-path th=3e-3 at 0.9792 vs exact. mps chi=16 got 0.9527 and pauli-path 1e-2 got 0.8928. The pauli 3e-3 + mps chi=16 blend got 0.9740, below its better input. That blend agrees 0.9917 with each input, but that agreement says nothing about accuracy. Validated READY: 16 rows, IDs Rehearsal_S2_0..15.
- Method note: Exact statevector simulation of each of the 16 provided QASM files (one per probe qubit), run locally; ⟨Z_q⟩ on the probe, no truncation.

## S3 (open sample, 20 qubits, order 2)

- File: <kit>/work/csv/S3__exact_local.csv
- Uploads used: 1 of 10 (this pick, rehearsal score 1.0000). Done, no second upload.
- Why: Exact answer. `S3__cpu.csv` (BlueQubit cpu statevector, run by ID) is byte-identical, so it scores the same and must not be uploaded as a second try. It does show that the cpu-by-ID path gets qubit order right on an order-2 circuit, which is the path G1 and G2 use. Best approximate was mps chi=64 at 0.9928. The mps chi=64 + pauli 1e-3 blend got 0.9780, below mps alone, even though it agreed 0.9864 with each input. mps chi=16 vs chi=64 agreed only 0.9492 even though chi=64 was at 0.993, so neighbour agreement was a poor guide here. Validated READY: 20 rows, IDs Rehearsal_S3_0..19.
- Method note: Exact statevector simulation of each of the 20 provided QASM files (one per probe qubit), run locally and cross-checked against BlueQubit's cpu statevector device (identical values); no truncation.

## G1 (graded, 300 points, 20 qubits, order 1)

- File: <kit>/work/csv/G1__cpu.csv
- Uploads used: 1 of 10 (this pick, rehearsal score 1.0000, so 300 of 300 points). Done, no second upload.
- Why: This is the exact statevector from BlueQubit's cpu device, run by ID. On S1 and S3 the same device and path matched exact_local byte for byte. All 20 jobs COMPLETED at $0.00, and every CSV value matches its job in `work/jobs.jsonl`. It's the only candidate, so there's nothing to blend. Validated READY: 20 rows, IDs Rehearsal_G1_0..19.
- Method note: Exact statevector simulation on BlueQubit's cpu device, one job per qubit, reading ⟨Z_q⟩ directly; no truncation or approximation.

## G2 (graded, 600 points, 24 qubits, order 2)

- File: <kit>/work/csv/G2__cpu.csv
- Uploads used: 1 of 10 (this pick, rehearsal score 1.0000, so 600 of 600 points). Done, no second upload.
- Why: Same path as G1. This is the exact statevector from BlueQubit's cpu device, run by ID, which matched exact on S1 and S3. All 24 jobs COMPLETED at $0.072 total, and every CSV value matches its job in `work/jobs.jsonl`. It's the only candidate. Validated READY: 24 rows, IDs Rehearsal_G2_0..23.
- Method note: Exact statevector simulation on BlueQubit's cpu device, one 24-qubit circuit per probe qubit, reading ⟨Z_q⟩ directly; no truncation or approximation.

## Phase 4 cross-checks (captain, rehearsal, 2026-11-20)

- G1 mps.cpu chi=64: corr² 0.9831 vs G1__cpu.csv. G2 mps.cpu chi=64: 0.9675 vs G2__cpu.csv. Since cpu scored 1.0000, these are what MPS alone would have scored.
- Picks above stand. Don't upload either MPS file: it can only score lower.
- Lesson for live: at 24 qubits, order 2, depth 32, chi=64 is about 0.97, so a live circuit too big for cpu needs chi 256 or higher from the start.
- Lesson for live: `run --no-wait` skips the `--max-cost` check (bq_kit.py lines 350-351 `continue` before costs are counted), and spend excludes uncollected jobs. Overnight: submit 3 qubits with --no-wait, `collect`, read the real cost, then submit the rest.
