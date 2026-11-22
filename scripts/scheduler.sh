#!/bin/bash
# Keep BlueQubit's 5 job slots busy across a task list, using only kit commands:
#   run <task> --qubits a,b,c --no-wait   (submits only what fits in free slots)
#   collect                              (gathers finished jobs, builds CSVs)
# A `run` call can take minutes (it prices in-flight jobs at new settings with server estimates),
# so `run` is only called when the ledger shows a free slot. Each pass: collect, count free
# slots locally, then give them to tasks in file order (up to PER_PASS qubits per task).
# A qubit whose setting BlueQubit refuses goes to work/rejected.txt and is skipped.
# Usage: bash work/scheduler.sh work/tasks.txt [max_minutes] [cap_per_run_usd]
cd "$(dirname "$0")/.." || exit 1
unset BQ_MODE
TASKS="$1"; MAX_MIN="${2:-120}"; CAP="${3:-10}"
SLOTS=$(python3 -c "import json; print(int(json.load(open('config.json')).get('max_parallel', 5)))")
STATUS="work/.sched_status"
start=$(date +%s)
echo "== $(TZ=America/Phoenix date '+%H:%M:%S') scheduler start: $TASKS, max ${MAX_MIN} min, cap \$${CAP} per run, ${SLOTS} slots"
while :; do
  python kit/bq_kit.py collect 2>&1 | grep -v BQ-PYTHON | grep -E "wrote|STOPPED|rror|Trace" | sed "s|^|  [collect] |"
  plan=$(python3 work/sched_tasks.py "$TASKS" 2>"$STATUS")
  # An empty or finished task list means "wait for new lines", not "stop": the task file is
  # edited live, and jobs still in flight need collecting. Only the time limit ends the loop.
  if [ "$plan" = "ALLDONE" ]; then plan=""; fi
  busy=$(python3 work/sched_inflight.py)
  if [ -n "$plan" ] && [ "$busy" -lt "$SLOTS" ]; then
    while IFS='|' read -r args qs; do
      [ -z "$args" ] && continue
      busy=$(python3 work/sched_inflight.py)
      [ "$busy" -ge "$SLOTS" ] && break
      c=${args%% *}
      tag=$(python3 work/sched_tag.py "$args")
      out=$(python kit/bq_kit.py run $args --qubits "$qs" --no-wait --max-cost "$CAP" 2>&1 | grep -v BQ-PYTHON)
      if echo "$out" | grep -q "FAILED_VALIDATION"; then
        bad=$(python3 work/sched_first_missing.py "$c" "$tag" "$qs")
        [ -n "$bad" ] && echo "$c $tag $bad" >> work/rejected.txt
        echo "  [$args q$bad] refused by BlueQubit (FAILED_VALIDATION), skipped"
      elif echo "$out" | grep -q "NOT_ENOUGH_FUNDS"; then
        bad=$(python3 work/sched_first_missing.py "$c" "$tag" "$qs")
        [ -n "$bad" ] && echo "$c $tag $bad" >> work/rejected.txt
        echo "  [$args q$bad] refused: too large for the free tier (NOT_ENOUGH_FUNDS), skipped"
      elif echo "$out" | grep -qE "Traceback|STOPPED|Budget|rror"; then
        echo "  [$args q$qs] $(echo "$out" | grep -E 'STOPPED|Budget|rror' | tail -1 | cut -c1-200)"
      else
        echo "  [$args] from $qs: $(echo "$out" | grep -E 'submitted' | tail -1 | cut -c1-80)"
      fi
    done <<< "$(echo "$plan" | grep -v '^ALLDONE')"
  fi
  echo "-- $(TZ=America/Phoenix date '+%H:%M:%S') busy $(python3 work/sched_inflight.py)/$SLOTS"; cat "$STATUS"
  if [ $(( $(date +%s) - start )) -gt $(( MAX_MIN * 60 )) ]; then echo "== time limit reached"; break; fi
  sleep 10
done
