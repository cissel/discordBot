#!/usr/bin/env bash
# run_sweep_night.sh
# =====================
# Overnight sweep: (1) 5-seed replication of the current best config
# (mixed, 64,64, 2M steps) to check whether the measured edge is real or one
# lucky seed, then (2) a hyperparameter grid over ent-coef x net-arch x
# historical_frac at a reduced step budget for fast screening. Every trained
# model gets evaluated with eval_mm_policy.py on BOTH synthetic and
# historical episodes, with every result appended to one long-format CSV
# as it finishes (so an interrupted run loses at most the in-flight model,
# not the whole night).
#
# Run from the discordBot repo root, under caffeinate so the Mac doesn't
# sleep mid-sweep:
#
#   cd ~/discordBot
#   caffeinate -ims bash python/rl/run_sweep_night.sh 2>&1 | tee /tmp/sweep_night.log
#
# (-i = no idle sleep, -m = no disk sleep, -s = no system sleep on AC power.
#  Keep it plugged in.)
#
# All 5 seeds are trained FRESH in this run (not reusing the existing
# Pi-trained ppo_mm_mixed_2000000.zip) so the seed-sensitivity check isn't
# confounded by Pi-vs-Mac hardware/torch differences - same machine, same
# everything except --seed.

set -uo pipefail  # NOT -e: one failed config shouldn't kill the whole night

cd "$(dirname "$0")/../.." || exit 1   # -> discordBot repo root
PY=venv/bin/python3
RL=python/rl
STAMP=$(date +%Y%m%d_%H%M)
RESULTS_CSV="$RL/models/sweep_results_${STAMP}.csv"
N_ENVS=${N_ENVS:-4}   # bump this (export N_ENVS=8) if the Mac has more cores than the Pi's 4

SYN_EPISODES=200
HIST_EPISODES=40   # 19 real historical days available, sampled with replacement

echo "=== Sweep starting $(date) ==="
echo "Results CSV: $RESULTS_CSV"
echo "N_ENVS=$N_ENVS"

run_one() {
    # args: group tag timesteps net_arch ent_coef hist_frac seed
    local group=$1 tag=$2 timesteps=$3 net_arch=$4 ent_coef=$5 hist_frac=$6 seed=$7
    local model_path="$RL/models/ppo_mm_mixed_${timesteps}_${tag}.zip"

    echo ""
    echo "--- [$group] $tag : timesteps=$timesteps net_arch=$net_arch ent_coef=$ent_coef hist_frac=$hist_frac seed=$seed ---"
    echo "    $(date)"

    if [ -f "$model_path" ]; then
        echo "    model already exists at $model_path, skipping training"
    else
        $PY "$RL/train_mm_rl.py" \
            --timesteps "$timesteps" --mode mixed --seed "$seed" \
            --net-arch "$net_arch" --ent-coef "$ent_coef" --historical-frac "$hist_frac" \
            --n-envs "$N_ENVS" --tag-suffix "_${tag}"
        if [ $? -ne 0 ]; then
            echo "    !!! TRAINING FAILED for $tag, skipping eval"
            return 1
        fi
    fi

    for mode in synthetic historical; do
        episodes=$SYN_EPISODES
        [ "$mode" = "historical" ] && episodes=$HIST_EPISODES
        $PY "$RL/eval_mm_policy.py" \
            --model "$model_path" --mode "$mode" --episodes "$episodes" \
            --out-csv "$RESULTS_CSV" --group "$group" --tag "$tag" \
            --config-seed "$seed" --ent-coef "$ent_coef" --net-arch "$net_arch" \
            --historical-frac-cfg "$hist_frac" --train-timesteps "$timesteps"
    done
}

# ── 1. Seed replication: current best config (mixed, 64,64, 2M steps) ──────
# ent_coef=0.0, net_arch=64,64, historical_frac=0.3 match the existing
# validated ppo_mm_mixed_2000000.zip; only --seed varies.
for seed in 42 43 44 45 46; do
    run_one "seed_sweep" "seed${seed}" 2000000 "64,64" 0.0 0.3 "$seed"
done

# ── 2. Hyperparameter grid (fast screen) ────────────────────────────────────
# ent-coef x net-arch x historical_frac, 18 configs at a reduced step budget
# so the grid can plausibly finish in one night. Seed fixed at 42 (the seed
# sweep above already covers seed variance for the baseline config).
GRID_TIMESTEPS=300000
for ent_coef in 0.0 0.005 0.01; do
    for net_arch in "64,64" "128,128"; do
        for hist_frac in 0.2 0.3 0.5; do
            na_tag=$(echo "$net_arch" | tr -d ',')
            ec_tag=$(echo "$ent_coef" | tr -d '.')
            hf_tag=$(echo "$hist_frac" | tr -d '.')
            tag="grid_ec${ec_tag}_na${na_tag}_hf${hf_tag}"
            run_one "grid_sweep" "$tag" "$GRID_TIMESTEPS" "$net_arch" "$ent_coef" "$hist_frac" 42
        done
    done
done

echo ""
echo "=== Sweep finished $(date) ==="
echo "Raw long-format results: $RESULTS_CSV"

# ── 3. Build the final ranked CSV ───────────────────────────────────────────
$PY "$RL/aggregate_sweep_results.py" --in-csv "$RESULTS_CSV" \
    --out-csv "${RESULTS_CSV%.csv}_ranked.csv"
echo "Ranked results: ${RESULTS_CSV%.csv}_ranked.csv"
