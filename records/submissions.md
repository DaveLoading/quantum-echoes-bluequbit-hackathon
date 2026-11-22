# Submissions log

What David uploaded and the score the site showed.

| Time (Phoenix) | Circuit | File | Score shown | Upload # | Note |
| --- | --- | --- | --- | --- | --- |
| 01:22 PM | S1 | work/csv/S1__exact_local.csv | 100/100 (time penalty 0.37 h) | 1 of 10 | Uploaded by Claude in Browser 1 at David's request. Exact statevector of the provided QASM; matches BlueQubit cpu by ID |
| 01:22 PM | S2 | work/csv/S2__exact_local.csv | 100/100 (time penalty 0.37 h) | 1 of 10 | Uploaded by Claude in Browser 1 at David's request. Exact statevector of the provided QASM |
| 01:25 PM | S3 | work/csv/S3__exact_local.csv | 100/100 (time penalty 0.42 h) | 1 of 10 | Uploaded by Claude (standing OK). Exact statevector of the provided QASM |
| 01:38 PM | G1 | work/csv/G1__cpu.csv | 300/300 (time penalty 0.65 h) | 1 of 10 | Uploaded by Claude (standing OK). Exact statevector on BlueQubit cpu by ID, $0 |
| 01:55 PM | G3 | work/csv/G3__pauli-path_th0.001.csv | 80/800 (time penalty 0.92 h) | 1 of 10 | Pauli-path th 1e-3: nearly all values exactly 0 (pruned away). Floor only |
| 04:01 PM | G2 | work/csv/G2__mps.cpu_chi16.csv | 300/600 (time penalty 3.03 h) | 1 of 10 | MPS chi 16 by ID. Fable loaded the file and note but its Submit click was blocked at 3:46 PM; the submission at 4:01 PM was made from the browser, not by Fable. corr2 0.5 to 0.6: the MPS step is partly fake |
| 05:15 PM | G3 | work/csv/G3__pp-level0_mixed_v1.csv | 320/800 (time penalty 4.25 h) | 2 of 10 | Uploaded by Fable. Pauli-path level 0: th 1e-3 on all, 1e-4 on q4, 8, 15, 29, 5e-5 on q3. corr2 0.4 to 0.5: the 25 exact zeros are pruning, not real zeros |
| 06:05 PM | G2 | work/csv/G2__pauli-path_level0_th0.001.csv | 0/600 | 2 of 10 | Uploaded by Fable on David's "submit G2". Pauli-path level 0 th 1e-3: only q22 to q25 non-zero. corr2 under 0.1, so the 10-qubit block that Pauli-path prunes to zero is truly raised. Best stays 300 |
| 06:59 PM | G2 | work/csv/G2__mps-chi24_pp-level0-1e-4-q22-25.csv | 540/600 (time penalty 5.99 h) | 3 of 10 | Uploaded by Fable. MPS chi 24 on q0 to q21 and q26 to q35, Pauli-path level 0 th 1e-4 on q22 to q25. corr2 0.9+ |
| 08:49 PM | G4 | work/csv/G4__pp-level0-1_th1e-4.csv | 400/1000 (time penalty 7.83 h) | 1 of 10 | Uploaded by Fable. Pauli-path th 1e-4, level 0 on 24 qubits, level 1 on 12. corr2 0.4 |
| 10:13 PM | G4 | work/csv/G4__pp-level3_th1e-4.csv | 700/1000 (time penalty 9.23 h) | 2 of 10 | Uploaded by Fable. Pauli-path th 1e-4, transpilation level 3 on all 36. corr2 0.7 |
| 10:57 PM | G3 | work/csv/G3__pp-level3_th1e-4.csv | 720/800 (time penalty 9.96 h) | 3 of 10 | Uploaded by Fable. Pauli-path th 1e-4, transpilation level 3 on all 36. corr2 0.9 |
| 12:05 AM Nov 22 | G4 | work/csv/G4__pauli-path_level3_th7e-05.csv | 700/1000 (time penalty 11.10 h) | 3 of 10 | Uploaded by Fable. Pauli-path level 3, th 7e-5 on all 36. No gain over the 1e-4 level 3 file |
| 9:55 AM Nov 22 | G4 | work/csv/G4__pp-level3_extrap-th0_from-1e-4-7e-5.csv | 800/1000 (time penalty 20.93 h) | 4 of 10 | Uploaded by Fable. Pauli-path level 3, linear extrapolation in threshold from 1e-4 and 7e-5 to zero truncation |
| 10:14 AM Nov 22 | G4 | work/csv/G4__pp-level3_quad-extrap-th0_from-2e-4-1e-4-7e-5.csv | 800/1000 (time penalty 21.23 h) | 5 of 10 | Uploaded by Fable. Pauli-path level 3, quadratic extrapolation in threshold (2e-4, 1e-4, 7e-5) to zero truncation. No gain over the linear version |
| 10:48 AM Nov 22 | G4 | work/csv/G4__pp-level3_logfit_th1e-5.csv | 700/1000 (time penalty 21.88 h) | 6 of 10 | Uploaded by Fable. Per-qubit log-linear fit in threshold evaluated at 1e-5. Worse than the 800 files: extrapolating past 3e-5 overshoots |
| 11:21 AM Nov 22 | G4 | work/csv/G4__pp-level3_at3e-5_measured-plus-loglin_v2.csv | 700/1000 (time penalty 22.35 h) | 7 of 10 | Uploaded by Fable. Measured 3e-5 values on 7 qubits, log-linear placement at 3e-5 on the rest. Below the 800 files |
| 11:50 AM Nov 22 | G3 | work/csv/G3__pp-1e-4_level01_plus_level3-q6-7-28.csv | 720/800 (time penalty 22.85 h) | 4 of 10 | Uploaded by Fable. Level 0/1 values where stable, level 3 on q6, 7, 28. Same score as the level 3 file |
| 11:54 AM Nov 22 | G2 | work/csv/G2__mps-mean-chi21-24_pp-level0-1e-4-q22-25.csv | 540/600 (time penalty 22.90 h) | 4 of 10 | Uploaded by Fable. Mean of MPS chi 21 to 24 on 32 qubits, Pauli-path on q22 to 25. Same score |
| 12:23 PM Nov 22 | G4 | work/csv/G4__pp-level3_richardson-finest-pair_v2_scaled.csv | 700/1000 (time penalty 23.40 h) | 8 of 10 | Uploaded by Fable. Richardson extrapolation from the finest measured pair per qubit. Below the 800 files |
| 12:29 PM Nov 22 | G2 | work/csv/G2__mps-mean-q12-21_pp-q22-25_pp-outer.csv | 540/600 (time penalty 23.50 h) | 5 of 10 | Uploaded by Fable. MPS mean on q12 to 21, Pauli-path on q22 to 25 and on the outer segments. Same score |
