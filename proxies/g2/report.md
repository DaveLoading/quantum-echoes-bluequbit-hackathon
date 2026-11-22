# G2 proxy: which cheap method keeps the pattern (2026-11-21, local only, $0)

Saved by the captain from the proxy agent's reply (subagents can't write report files).

- Build: 20-qubit open chain, 20 brickwork layers per U, Haar 2-qubit gates (3 CZ + 8 u3 each, like S2), G2's order-2 echo with butterflies on 2 and 15. Three same-parity weak bonds at 5|6, 11|12, 17|18 using exp(-i eps H) with a random two-body H: PA eps 0.1 (hard cut), PB eps 0.3 (leaky).
- Exact patterns: PA shows a clear step (about +0.35) on the segments with no butterfly, near 0 elsewhere. PB has no step; everything is within ±0.08.

| Method | Setting | PA (hard cut) | PB (leaky) | S2 (no weak bonds) |
| --- | --- | --- | --- | --- |
| MPS | chi 8 | 0.975 | 0.000 (fake step) | 0.54 |
| MPS | chi 16 | 0.956 | 0.000 (fake step) | 0.65 |
| MPS | chi 32 | 0.999 | 0.584 | 0.78 |
| MPS | chi 64 | 1.000 | 0.696 | 0.93 |
| Pauli-path | th 1e-2 | 0.080 | 0.000 (all zeros) | 0.000 |
| Pauli-path | th 1e-3 | 0.863 | 0.000 (all zeros) | 0.000 |
| Pauli-path | th 3e-4 | 0.947 | not run | not run |

- Findings: MPS keeps a real step even at low chi, but fakes one when bonds leak. Pauli-path is all or nothing per segment and never invents a step. The G2 gate counts fit exactly 3 specially built weak bonds (6 CZ per gate instead of 3) [Guessing, arithmetic fits].
- Recommendation: Pauli-path th 1e-5 first (test one middle qubit for the 15-minute limit), MPS chi 16 as the cross-check. Trust a block of raised values only when both agree.
- Files: proxy_g2.py, qasm/PA, qasm/PB, csv/, results.jsonl, proxy_meta.json.
