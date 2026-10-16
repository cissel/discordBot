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
import numpy as np
from stable_baselines3 import PPO

from mm_env import MarketMakerEnv, MMEnvConfig

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
    print(f"Paired diff: mean={diff.mean():.3f}  std={diff.std():.3f}  "
          f"(positive = RL beats baseline on same price path)")


if __name__ == "__main__":
    main()
