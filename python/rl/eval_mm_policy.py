"""
eval_mm_policy.py
==================
Compare a trained RL policy against the CURRENT PRODUCTION RULE used by
marketMaker.py ("always quote at the touch both sides, skip a side past the
inventory cap, no skew") on the same simulated/historical episodes, so we
can tell whether RL is actually worth deploying before touching the live
paper bot.

Usage:
  venv/bin/python3 python/rl/eval_mm_policy.py --model python/rl/models/ppo_mm_synthetic_300000.zip --mode synthetic --episodes 200
  venv/bin/python3 python/rl/eval_mm_policy.py --model python/rl/models/ppo_mm_historical_300000.zip --mode historical --episodes 16
"""

import argparse
import csv
import os
import numpy as np
from stable_baselines3 import PPO

from mm_env import MarketMakerEnv, MMEnvConfig

CSV_FIELDS = [
    "group", "tag", "mode", "episodes", "config_seed", "ent_coef", "net_arch",
    "historical_frac_cfg", "train_timesteps", "model_path",
    "rl_mean", "rl_std", "rl_median",
    "base_mean", "base_std", "base_median",
    "diff_mean", "diff_std", "tstat", "win_rate",
]


def append_csv_row(path, row):
    """Append one eval result row, writing the header if the file is new.
    One row per (model, eval-mode) so a crashed/interrupted overnight sweep
    loses at most the in-progress run, not everything before it."""
    file_exists = os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            w.writeheader()
        w.writerow(row)

# Fixed baseline matches marketMaker.py's actual logic: quote AT the market
# touch (half_spread_bps = half the market spread) with zero skew. The env's
# fill model already keys fill probability off (quote_distance - market_half_
# spread), so half_spread_bps == market_half_spread reproduces "quote at the
# bid/ask" behavior.
def baseline_action(obs, market_spread_bps):
    half_spread = market_spread_bps / 2.0
    return np.array([half_spread, 0.0], dtype=np.float32)


def run_episode(env, policy_fn):
    obs, _ = env.reset()
    total_reward = 0.0
    while True:
        market_spread_bps = env.spread_path[env.t] if env.cfg.mode != "historical" else env.spread_path[env.t]
        if policy_fn == "baseline":
            action = baseline_action(obs, market_spread_bps)
        else:
            action, _ = policy_fn.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(action)
        total_reward += r
        if term or trunc:
            return total_reward, info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=["synthetic", "historical", "mixed"], default="synthetic")
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--out-csv", type=str, default=None,
                         help="append a results row (config metadata + stats) to this CSV")
    parser.add_argument("--group", type=str, default="",
                         help="sweep group label, e.g. 'seed_sweep' or 'grid_sweep' (metadata only)")
    parser.add_argument("--tag", type=str, default="", help="config tag, e.g. 'seed43' (metadata only)")
    parser.add_argument("--config-seed", type=int, default=None,
                         help="the --seed the MODEL was trained with (metadata only, distinct from "
                              "--seed above which seeds eval episodes)")
    parser.add_argument("--ent-coef", type=float, default=None, help="training ent_coef (metadata only)")
    parser.add_argument("--net-arch", type=str, default=None, help="training net_arch (metadata only)")
    parser.add_argument("--historical-frac-cfg", type=float, default=None,
                         help="training historical_frac (metadata only)")
    parser.add_argument("--train-timesteps", type=int, default=None, help="training timesteps (metadata only)")
    args = parser.parse_args()

    model = PPO.load(args.model)

    env = MarketMakerEnv(MMEnvConfig(mode=args.mode, seed=args.seed))

    rl_rewards, base_rewards = [], []
    for ep in range(args.episodes):
        env._rng = np.random.default_rng(args.seed + ep)
        r, info = run_episode(env, model)
        rl_rewards.append(r)

        env._rng = np.random.default_rng(args.seed + ep)  # SAME seed -> same price path
        r2, info2 = run_episode(env, "baseline")
        base_rewards.append(r2)

    rl_rewards = np.array(rl_rewards)
    base_rewards = np.array(base_rewards)

    print(f"Episodes: {args.episodes}  mode={args.mode}")
    print(f"RL policy:   mean={rl_rewards.mean():.3f}  std={rl_rewards.std():.3f}  "
          f"median={np.median(rl_rewards):.3f}  win_rate_vs_baseline="
          f"{(rl_rewards > base_rewards).mean()*100:.1f}%")
    print(f"Baseline:    mean={base_rewards.mean():.3f}  std={base_rewards.std():.3f}  "
          f"median={np.median(base_rewards):.3f}")
    diff = rl_rewards - base_rewards
    n = len(diff)
    se = diff.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    tstat = diff.mean() / se if se and se > 0 else float("nan")
    print(f"Paired diff: mean={diff.mean():.3f}  std={diff.std():.3f}  tstat={tstat:.2f}  "
          f"(positive = RL beats baseline on same price path)")

    if args.out_csv:
        row = {
            "group": args.group,
            "tag": args.tag,
            "mode": args.mode,
            "episodes": args.episodes,
            "config_seed": args.config_seed,
            "ent_coef": args.ent_coef,
            "net_arch": args.net_arch,
            "historical_frac_cfg": args.historical_frac_cfg,
            "train_timesteps": args.train_timesteps,
            "model_path": args.model,
            "rl_mean": rl_rewards.mean(), "rl_std": rl_rewards.std(), "rl_median": np.median(rl_rewards),
            "base_mean": base_rewards.mean(), "base_std": base_rewards.std(), "base_median": np.median(base_rewards),
            "diff_mean": diff.mean(), "diff_std": diff.std(), "tstat": tstat,
            "win_rate": (rl_rewards > base_rewards).mean() * 100,
        }
        append_csv_row(args.out_csv, row)
        print(f"Appended result row to {args.out_csv}")


if __name__ == "__main__":
    main()
