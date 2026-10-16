"""
train_mm_rl.py
==============
Train a PPO policy on MarketMakerEnv (python/rl/mm_env.py) and save it.

This is a SIMULATION-ONLY training run. Nothing here touches the live paper
account or marketMaker.py - it's a separate research track to see whether a
learned quoting policy can beat the current fixed "always quote at the
touch, skip past inventory cap" rule used in production.

Usage:
  venv/bin/python3 python/rl/train_mm_rl.py --timesteps 300000 --mode synthetic
  venv/bin/python3 python/rl/train_mm_rl.py --timesteps 300000 --mode historical

Output:
  python/rl/models/ppo_mm_<mode>_<timesteps>.zip
  python/rl/models/ppo_mm_<mode>_<timesteps>_training_log.csv  (episode reward curve)
"""

import os
import argparse
import csv

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback

from mm_env import MarketMakerEnv, MMEnvConfig

MODEL_DIR = os.path.expanduser("~/discordBot/python/rl/models")


class EpisodeLogCallback(BaseCallback):
    """Lightweight CSV logger of episode reward/length, since the Pi has no
    TensorBoard viewer handy - a CSV is easy to eyeball or plot later."""

    def __init__(self, log_path, verbose=0):
        super().__init__(verbose)
        self.log_path = log_path
        self._rows = []

    def _on_step(self) -> bool:
        for info in self.locals.get("infos", []):
            ep = info.get("episode")
            if ep:
                self._rows.append({"timesteps": self.num_timesteps, "ep_reward": ep["r"], "ep_len": ep["l"]})
        return True

    def _on_training_end(self) -> None:
        if not self._rows:
            return
        with open(self.log_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["timesteps", "ep_reward", "ep_len"])
            w.writeheader()
            w.writerows(self._rows)


def make_env(mode, seed):
    def _init():
        env = MarketMakerEnv(MMEnvConfig(mode=mode, seed=seed))
        return Monitor(env)
    return _init


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=300_000)
    parser.add_argument("--mode", choices=["synthetic", "historical", "mixed"], default="synthetic")
    parser.add_argument("--n-envs", type=int, default=4, help="parallel envs (Pi has 4 cores)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--net-arch", type=str, default="64,64",
                         help="comma-separated hidden layer sizes, e.g. '128,128' (default matches "
                              "the validated v3/v6 models: '64,64')")
    parser.add_argument("--ent-coef", type=float, default=0.0,
                         help="entropy bonus coefficient (default 0.0, matches validated v3/v6 models). "
                              "Small nonzero values (e.g. 0.005) discourage premature convergence.")
    parser.add_argument("--tag-suffix", type=str, default="",
                         help="appended to the output model filename, e.g. '_hptest', so experimental "
                              "runs don't overwrite the canonical ppo_mm_<mode>_<timesteps>.zip")
    args = parser.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)

    net_arch = [int(x) for x in args.net_arch.split(",")]

    env_fns = [make_env(args.mode, args.seed + i) for i in range(args.n_envs)]
    vec_env = DummyVecEnv(env_fns) if args.n_envs == 1 else SubprocVecEnv(env_fns)

    model = PPO(
        "MlpPolicy",
        vec_env,
        learning_rate=3e-4,
        n_steps=256,       # short rollouts since episodes are ~60 steps
        batch_size=256,
        n_epochs=10,
        gamma=0.995,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=args.ent_coef,
        policy_kwargs=dict(net_arch=net_arch),
        verbose=1,
        seed=args.seed,
    )

    tag = f"ppo_mm_{args.mode}_{args.timesteps}{args.tag_suffix}"
    log_path = os.path.join(MODEL_DIR, f"{tag}_training_log.csv")
    callback = EpisodeLogCallback(log_path)

    model.learn(total_timesteps=args.timesteps, callback=callback, progress_bar=False)

    model_path = os.path.join(MODEL_DIR, f"{tag}.zip")
    model.save(model_path)
    print(f"Saved model to {model_path}")
    print(f"Episode log at {log_path}")
    print(f"Config: net_arch={net_arch} ent_coef={args.ent_coef}")


if __name__ == "__main__":
    main()
