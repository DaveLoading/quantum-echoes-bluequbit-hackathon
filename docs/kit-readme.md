# BlueQubit hackathon agent kit

A captain (your main Claude Code session) plus four specialist agents, sharing one toolkit that handles budget, throttling, file names and qubit order.

## Setup (10 minutes)

1. Copy this folder to where you'll work: your BlueQubit Hybrid machine (Claude Code is preinstalled there) or your own computer.
2. Install: `pip install -r requirements.txt`
3. Token: on Hybrid it's already set. On your own computer, copy it from BlueQubit (top bar, See API token) and run `export BLUEQUBIT_API_TOKEN=...` in the terminal before starting Claude Code. Never paste it into a chat.
4. Start Claude Code in this folder: `claude`. It reads `CLAUDE.md` and finds the agents in `.claude/agents/`.

## Rehearse tonight (free, offline)

```
export BQ_MODE=rehearsal
claude
```

Then tell the captain:

> Rehearsal. The circuits are S1, S2, S3 (open samples in kit/rehearsal/samples/) and G1 (300 points), G2 (600 points). Run the full plan through Phase 4 with the team. Rehearsal budget is in config.json. Report what you'd have me upload for every circuit and the scores rehearsal-score gives.

Practice circuits: 12 to 24 qubits, OTOC orders 1 and 2. `python kit/make_rehearsal.py` rebuilds them (about 6 minutes).

## Kickoff (Sat 2:00 pm CT, 1:00 pm Phoenix)

```
unset BQ_MODE
claude
```

> We're live. I'll paste the organizers' kickoff notes and the Problems tab. Update config.json, then start Phase 1.

## Toolkit cheat sheet

```
python kit/bq_kit.py status                                  # spend, circuits, CSVs
python kit/bq_kit.py info S1 G1 G2                           # free circuit facts
python kit/bq_kit.py exact S1                                # local truth for an open sample
python kit/bq_kit.py run G1 --device pauli-path --opt th=1e-3 --max-cost 2
python kit/bq_kit.py run G2 --device mps.cpu --opt chi=128 --no-wait
python kit/bq_kit.py collect                                 # gather --no-wait results
python kit/bq_kit.py candidates G1                           # compare every CSV for G1
python kit/bq_kit.py blend G1 work/csv/G1__a.csv work/csv/G1__b.csv
python kit/bq_kit.py validate G1 work/csv/G1__blend_...csv   # READY or NOT READY
```

Option shorthands: `chi` (MPS bond dimension, up to 2048 on cpu), `th` (Pauli-path truncation threshold, 1e-5 and up, required above 13 qubits), `level` (Pauli-path transpilation level 0 to 3).
