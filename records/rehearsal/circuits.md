# Circuit map (rehearsal, mapped 2026-11-20 by circuit-scout)

Source: `bq_kit info` (free), `bq_kit points`, `bq_kit describe --show 2`, plus a text-only structure check of every sample QASM file (no simulation, no jobs).

| Circuit | Qubits | 2q depth | Gate counts | Order | Points | Tier |
| --- | --- | --- | --- | --- | --- | --- |
| G2 | 24 | 32 | u3 768, cz 304, x 2, z 1 | 2 (inferred from counts) | 600 | easy (24 q, exact possible; heaviest of the five) |
| G1 | 20 | 16 | u3 320, cz 124, x 1 | 1 (inferred from counts) | 300 | easy |
| S3 | 20 | 32 | u3 640, cz 248, x 2, z 1 | 2 (confirmed from QASM) | | easy |
| S2 | 16 | 24 | u3 384, cz 160, x 2, z 1 | 2 (confirmed from QASM) | | easy |
| S1 | 12 | 12 | u3 144, cz 54, x 1 | 1 (confirmed from QASM) | | easy |

## Sample structure (checked on every file, not just the 2 shown)

| Sample | Butterfly (x) | Probe (z) | Grid | One U block: 2q depth, cz | Mirror |
| --- | --- | --- | --- | --- | --- |
| S1 | q5, 1 x | none | 3x4 | 6, 27 | yes |
| S2 | q6, both x | on qubit q in file q, all 16 files | 4x4 | 6, 40 | yes |
| S3 | q7, both x | on qubit q in file q, all 20 files | 4x5 | 8, 62 | yes |

- Shape matches the brief: order 1 is U, x, U-inverse; order 2 is U, x, U-inverse, z on probe, U, x, U-inverse.
- Mirror check: with the x and z gates removed, every file cancels wire by wire to nothing (u3(t,p,l) against u3(-t,-l,-p), cz against cz). Every gate in the backward light cone of the first x has its inverse after the kick. For order 2, the circuit also mirrors about the z, and each u3 appears exactly twice, so both U copies are identical.
- Within a sample, all files are identical except where the z sits. For S1 (order 1) all 12 files are the same circuit, so one simulation gives every ID's value.
- The file order is a topological interleave, not block by block. The raw x positions (S1 at 80 of 199) are not at the midpoint. That is expected and harmless.
- The butterfly's backward light cone reaches all qubits in every sample, so dropping whole qubits saves nothing.
- No measure, barrier or other gate types in any file.

## Graded circuits: inferences from counts only [inferred]

- G1: 1 x and no z, so order 1. Per block: 160 u3, 62 cz, 2q depth 8. That is the same block size as S3's U (62 cz matches two passes over a 4x5 grid's 31 edges). If it follows S1, one simulation covers all 20 IDs.
- G2: 2 x and 1 z, so order 2. Per block: 192 u3, 76 cz, 2q depth 8. 76 = 2 x 38, the edge count of a 4x6 grid. S3's pattern with 4 more qubits. Expect 24 separate circuits, one per probe position.
- Butterfly and probe positions for G1 and G2 are unknown (no QASM).
