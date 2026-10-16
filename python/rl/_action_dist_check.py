"""
_action_dist_check.py
======================
Pull raw action distributions from a trained MM policy to see HOW it's using
the action space: is half_spread_bps reacting to market spread, and is
skew_bps actually tracking inventory (the thing that should drive inventory
mean-reversion)? Logs every (obs, action) pair across many episodes.
"""
import argparse
import numpy as np
import pandas as pd
from stable_baselines3 import PPO

from mm_env import MarketMakerEnv, MMEnvConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--episodes", type=int, default=300)
    parser.add_argument("--seed", type=int, default=777)
    args = parser.parse_args()

    model = PPO.load(args.model)
    env = MarketMakerEnv(MMEnvConfig(mode="synthetic", seed=args.seed))

    rows = []
    for ep in range(args.episodes):
        obs, _ = env.reset(seed=args.seed + ep)
        while True:
            action, _ = model.predict(obs, deterministic=True)
            half_spread_bps, skew_bps = float(action[0]), float(action[1])
            inv_norm = float(obs[0])
            inventory = env.inventory
            market_spread = env.spread_path[env.t]
            rows.append({
                "ep": ep, "t": env.t,
                "inv_norm": inv_norm,
                "inventory": inventory,
                "market_spread_bps": market_spread,
                "half_spread_bps": half_spread_bps,
                "skew_bps": skew_bps,
                "unrl_pnl_frac": float(obs[3]),
            })
            obs, r, term, trunc, info = env.step(action)
            if term or trunc:
                break

    df = pd.DataFrame(rows)
    print(f"total steps logged: {len(df)}  across {args.episodes} episodes\n")

    print("=== half_spread_bps stats ===")
    print(df["half_spread_bps"].describe())
    print("\ncorrelation half_spread_bps vs market_spread_bps:",
          df["half_spread_bps"].corr(df["market_spread_bps"]))

    print("\n=== skew_bps stats ===")
    print(df["skew_bps"].describe())
    print("\ncorrelation skew_bps vs inv_norm (inventory, should be negative if unwinding):",
          df["skew_bps"].corr(df["inv_norm"]))
    print("correlation skew_bps vs raw inventory:",
          df["skew_bps"].corr(df["inventory"]))

    # bucket by inventory level to see conditional mean skew
    df["inv_bucket"] = pd.cut(df["inv_norm"], bins=[-1.5, -0.66, -0.33, 0.33, 0.66, 1.5],
                                labels=["very_short", "short", "neutral", "long", "very_long"])
    print("\n=== mean skew_bps by inventory bucket (expect negative skew when very_long, positive when very_short) ===")
    print(df.groupby("inv_bucket", observed=True)["skew_bps"].agg(["mean", "std", "count"]))

    print("\n=== mean half_spread_bps by inventory bucket ===")
    print(df.groupby("inv_bucket", observed=True)["half_spread_bps"].agg(["mean", "std", "count"]))

    # how often is skew near its bounds (saturation) vs near 0 (not using it)
    print("\n=== skew_bps distribution shape ===")
    print("pct near 0 (|skew|<0.5):", (df["skew_bps"].abs() < 0.5).mean())
    print("pct saturated (|skew|>7.0):", (df["skew_bps"].abs() > 7.0).mean())
    print("unique skew values (rounded to 2dp), sample:", sorted(df["skew_bps"].round(2).unique())[:20])

    df.to_csv("/tmp/mm_action_dist.csv", index=False)
    print("\nFull per-step log saved to /tmp/mm_action_dist.csv")


if __name__ == "__main__":
    main()
