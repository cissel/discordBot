#!/bin/bash
cd /home/jhcv/discordBot
LOGDIR=/home/jhcv/discordBot/logs/weekly_retrain
TS=$(date +%Y%m%d_%H%M%S)
SUMMARY="$LOGDIR/summary_$TS.log"
echo "Weekly retrain pipeline started at $(date)" > "$SUMMARY"

run_step() {
  local name="$1"
  shift
  local logfile="$LOGDIR/${name}_$TS.log"
  echo "=== Running: $name ===" | tee -a "$SUMMARY"
  echo "Command: $*" >> "$logfile"
  START=$(date +%s)
  "$@" >> "$logfile" 2>&1
  local rc=$?
  END=$(date +%s)
  DUR=$((END-START))
  if [ $rc -eq 0 ]; then
    echo "[$name] SUCCESS (${DUR}s) -> $logfile" | tee -a "$SUMMARY"
  else
    echo "[$name] FAILED with exit code $rc (${DUR}s) -> $logfile" | tee -a "$SUMMARY"
    echo "--- last 30 lines of $name log ---" >> "$SUMMARY"
    tail -n 30 "$logfile" >> "$SUMMARY"
  fi
}

run_step "trainFantasyModel" venv/bin/python3 python/trainFantasyModel.py --notes "weekly retrain"
run_step "buildIntradayFeatures" venv/bin/python3 python/buildIntradayFeatures.py
run_step "buildSpyFeatures" venv/bin/python3 python/buildSpyFeatures.py
run_step "trainSpyModel" venv/bin/python3 python/trainSpyModel.py --notes "weekly retrain"
run_step "trainChopModel" venv/bin/python3 python/trainChopModel.py --notes "weekly retrain chop"
run_step "buildOvernightModel" venv/bin/python3 python/buildOvernightModel.py
run_step "buildRegimePredModel" venv/bin/python3 python/buildRegimePredModel.py
run_step "buildBtcFeatures" venv/bin/python3 python/buildBtcFeatures.py
run_step "trainBtcModel" venv/bin/python3 python/trainBtcModel.py --notes "weekly retrain"

echo "Weekly retrain pipeline finished at $(date)" | tee -a "$SUMMARY"
echo "SUMMARY_FILE:$SUMMARY"
