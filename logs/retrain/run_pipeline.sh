#!/bin/bash
cd /home/jhcv/discordBot
LOGDIR=/home/jhcv/discordBot/logs/retrain
TS=$(date +%Y%m%d_%H%M%S)
SUMMARY="$LOGDIR/summary_$TS.txt"
echo "Pipeline run started: $(date)" > "$SUMMARY"

run_step() {
  local step_name="$1"
  shift
  local logfile="$LOGDIR/${step_name}_${TS}.log"
  echo "=== Running: $step_name ==="
  echo "Command: $*" > "$logfile"
  START=$(date +%s)
  "$@" >> "$logfile" 2>&1
  RC=$?
  END=$(date +%s)
  DUR=$((END-START))
  if [ $RC -eq 0 ]; then
    echo "[OK] $step_name (${DUR}s) - log: $logfile" | tee -a "$SUMMARY"
  else
    echo "[FAIL] $step_name (exit $RC, ${DUR}s) - log: $logfile" | tee -a "$SUMMARY"
    echo "--- last 30 lines ---" >> "$SUMMARY"
    tail -30 "$logfile" >> "$SUMMARY"
    echo "--- end ---" >> "$SUMMARY"
  fi
}

PY=/home/jhcv/discordBot/venv/bin/python3

run_step "01_trainFantasyModel" $PY python/trainFantasyModel.py --notes "weekly retrain"
run_step "02_buildIntradayFeatures" $PY python/buildIntradayFeatures.py
run_step "03_buildSpyFeatures" $PY python/buildSpyFeatures.py
run_step "04_trainSpyModel" $PY python/trainSpyModel.py --notes "weekly retrain"
run_step "05_trainChopModel" $PY python/trainChopModel.py --notes "weekly retrain chop"
run_step "06_buildOvernightModel" $PY python/buildOvernightModel.py
run_step "07_buildRegimePredModel" $PY python/buildRegimePredModel.py
run_step "08_buildBtcFeatures" $PY python/buildBtcFeatures.py
run_step "09_trainBtcModel" $PY python/trainBtcModel.py --notes "weekly retrain"

echo "Pipeline run finished: $(date)" >> "$SUMMARY"
echo "SUMMARY_FILE=$SUMMARY"
