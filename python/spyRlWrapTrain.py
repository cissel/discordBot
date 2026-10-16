#!/usr/bin/env python3
"""
spyRlWrapTrain.py
===================
Trains PPO to learn a position-size MULTIPLIER on top of the existing
hand-coded vol-targeted SPY sizing (spyVolTargetWFCV.py), using
spyRlWrapEnv.SpyWrapEnv, and evaluates OOS against that same baseline on
the identical WFCV folds used throughout.

This is the "wrap, not replace" follow-up to the Oct'26 full-replace RL
spike (spyRlSizingTrain.py / spyRlIntradayTrain.py), which went net
negative in both daily and 5-min variants (collapsed to all-flat on one
fold, actively destabilizing on others) because full replacement threw
away the existing kill switch / chop-VIX gate / bear-halving rails. The
wrap can't override those rails (it multiplies an already-gated position,
so gated days stay at exactly zero regardless of the agent's output) --
it can only scale UP or DOWN the rule's confidence on days the rule
already allows a position.

Same fold setup as the prior spikes:
  Eval A: RL-train on folds 1+2 (504 days) -> eval OOS on fold 3 (252 days)
  Eval B: RL-train on folds 1+2+3 (756 days) -> eval OOS on fold 4 (252 days)

Usage:
  venv/bin/python3 python/spyRlWrapTrain.py
"""
import os
import sys
import warnings

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

import torch
torch.set_num_threads(1)

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.monitor import Monitor

from spyVolTargetWFCV import (run_wfcv, attach_realized_vol, base_kelly_fraction,
                               apply_gates, backtest_positions, ann_sharpe, max_drawdown)
from spyRlWrapEnv import build_wrap_state_frame, SpyWrapEnv, TX_COST, ANNUAL

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_PLOT_A = os.path.expanduser("~/discordBot/outputs/markets/spy_rl_wrap_foldA.png")
OUT_PLOT_B = os.path.expanduser("~/discordBot/outputs/markets/spy_rl_wrap_foldB.png")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy_rl")
os.makedirs(MODEL_DIR, exist_ok=True)

TOTAL_TIMESTEPS = 150_000
SEED = 42


def make_env(next_ret, vt_pos, static, episode_len, random_start, seed):
    def _f():
        env = SpyWrapEnv(next_ret, vt_pos, static, episode_len=episode_len,
                          random_start=random_start, seed=seed)
        return Monitor(env)
    return _f


def train_ppo(next_ret_train, vt_pos_train, static_train, seed=SEED):
    env = DummyVecEnv([make_env(next_ret_train, vt_pos_train, static_train,
                                  episode_len=len(next_ret_train),
                                  random_start=True, seed=seed)])
    model = PPO("MlpPolicy", env, verbose=0, seed=seed,
                n_steps=256, batch_size=64, n_epochs=10,
                learning_rate=3e-4, gamma=0.99, policy_kwargs=dict(net_arch=[64, 64]))
    model.learn(total_timesteps=TOTAL_TIMESTEPS)
    return model


def eval_policy(model, next_ret_eval, vt_pos_eval, static_eval):
    env = SpyWrapEnv(next_ret_eval, vt_pos_eval, static_eval, episode_len=len(next_ret_eval),
                      random_start=False, seed=SEED)
    obs, _ = env.reset()
    positions, net_rets, mults = [], [], []
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        positions.append(info["position"])
        net_rets.append(info["net_ret"])
        mults.append(info["mult"])
        done = terminated or truncated
    return np.array(positions), np.array(net_rets), np.array(mults)


def summarize(daily_ret, label):
    sharpe = ann_sharpe(daily_ret)
    mdd = max_drawdown(daily_ret)
    total_ret = float(np.prod(1 + daily_ret) - 1)
    ann_ret = float(np.mean(daily_ret) * ANNUAL)
    print(f"  {label:<32} Sharpe {sharpe:+.3f}  MaxDD {mdd*100:+.2f}%  "
          f"TotalRet {total_ret*100:+.2f}%  AnnRet {ann_ret*100:+.2f}%")
    return dict(sharpe=sharpe, max_dd=mdd, total_return=total_ret, ann_return=ann_ret)


def run_fold_comparison(oos, target_ann_vol, train_fold_ids, eval_fold_id, label, plot_path):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    fold_arr = oos["fold"].values

    # Build the FULL-sample vol-targeted baseline position once (this is
    # the thing the agent wraps -- must match spyVolTargetWFCV exactly).
    raw_kelly = base_kelly_fraction(oos["p"].values)
    vol_scalar = target_ann_vol / oos["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    vt_pos_full = apply_gates(oos, raw_kelly * vol_scalar)

    dates, next_ret, vt_pos, static, feat_names = build_wrap_state_frame(
        oos, vt_pos_full, target_ann_vol)

    train_mask = np.isin(fold_arr, train_fold_ids)
    eval_mask = fold_arr == eval_fold_id
    print(f"  RL-train days: {train_mask.sum()} (folds {train_fold_ids})  "
          f"Eval days: {eval_mask.sum()} (fold {eval_fold_id})")

    model = train_ppo(next_ret[train_mask], vt_pos[train_mask], static[train_mask])
    rl_pos, rl_daily_ret, rl_mults = eval_policy(
        model, next_ret[eval_mask], vt_pos[eval_mask], static[eval_mask])

    # Baselines, restricted to the SAME eval fold's days
    oos_eval = oos[eval_mask].reset_index(drop=True)
    raw_kelly_eval = base_kelly_fraction(oos_eval["p"].values)
    base_pos = apply_gates(oos_eval, raw_kelly_eval)
    bt_base = backtest_positions(oos_eval, base_pos)

    vt_pos_eval = vt_pos[eval_mask]
    bt_vt = backtest_positions(oos_eval, vt_pos_eval)

    print()
    res_base = summarize(bt_base["daily_ret"], "Baseline (fixed 50% Kelly)")
    res_vt = summarize(bt_vt["daily_ret"], "Vol-targeted overlay (wrapped)")
    res_rl = summarize(rl_daily_ret, "RL-wrapped (multiplier on vol-tgt)")

    print(f"\n  Delta RL-wrap vs vol-targeted: Sharpe {res_rl['sharpe']-res_vt['sharpe']:+.3f}  "
          f"TotalRet {(res_rl['total_return']-res_vt['total_return'])*100:+.2f}pp")
    print(f"  Mean position: vol-tgt={vt_pos_eval.mean():.3f}  RL-wrapped={rl_pos.mean():.3f}")
    print(f"  Mean multiplier: {rl_mults.mean():.3f}  (1.0 = pure deference to the rule)  "
          f"std={rl_mults.std():.3f}")
    n_gated_zero = int((vt_pos_eval == 0).sum())
    n_rl_zero_on_gated = int((rl_pos[vt_pos_eval == 0] == 0).sum()) if n_gated_zero else 0
    print(f"  Gate integrity check: {n_gated_zero} days gated to zero by existing rule, "
          f"{n_rl_zero_on_gated}/{n_gated_zero} stayed zero after RL multiplier "
          f"(must be {n_gated_zero}/{n_gated_zero} -- multiplier can't override a gate)")

    NAVY, GRID = "#02233F", "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    eq_base = np.cumprod(1 + bt_base["daily_ret"])
    eq_vt = np.cumprod(1 + bt_vt["daily_ret"])
    eq_rl = np.cumprod(1 + rl_daily_ret)
    eval_dates = oos_eval["date"].values
    ax.plot(eval_dates, eq_base, color="#5DADE2", lw=1.6,
            label=f"Fixed 50% Kelly (Sharpe {res_base['sharpe']:+.2f})")
    ax.plot(eval_dates, eq_vt, color="#F5B041", lw=1.6,
            label=f"Vol-targeted (Sharpe {res_vt['sharpe']:+.2f})")
    ax.plot(eval_dates, eq_rl, color="#2E9E4E", lw=1.8,
            label=f"RL-wrapped multiplier (Sharpe {res_rl['sharpe']:+.2f})")
    ax.set_title(f"{label}\nRL multiplier-wrap vs hand-coded sizing, OOS eval fold (zero lookahead)",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative return (x)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"RL-train folds {train_fold_ids} ({train_mask.sum()}d) -> eval fold "
              f"{eval_fold_id} ({eval_mask.sum()}d) | TX_COST={TX_COST} one-way | "
              f"PPO {TOTAL_TIMESTEPS} timesteps, mult range [0,2], WINDOW=10",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(plot_path, facecolor=NAVY, dpi=140)
    print(f"\n  Saved plot: {plot_path}")

    model.save(os.path.join(MODEL_DIR, f"ppo_wrap_{label.split()[0].lower()}"))
    return dict(base=res_base, vt=res_vt, rl=res_rl)


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos = run_wfcv(df_raw)
    oos = attach_realized_vol(oos, df_raw)

    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"target_ann_vol (full-sample median): {target_ann_vol:.4f}")

    results = {}
    results["A"] = run_fold_comparison(oos, target_ann_vol, train_fold_ids=[1, 2],
                                        eval_fold_id=3, label="Fold A: train[1,2] -> eval[3]",
                                        plot_path=OUT_PLOT_A)
    results["B"] = run_fold_comparison(oos, target_ann_vol, train_fold_ids=[1, 2, 3],
                                        eval_fold_id=4, label="Fold B: train[1,2,3] -> eval[4]",
                                        plot_path=OUT_PLOT_B)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    for k, r in results.items():
        print(f"  Eval {k}: baseline Sharpe {r['base']['sharpe']:+.3f}  "
              f"vol-tgt Sharpe {r['vt']['sharpe']:+.3f}  "
              f"RL-wrap Sharpe {r['rl']['sharpe']:+.3f}  "
              f"(RL-wrap - vol-tgt: {r['rl']['sharpe']-r['vt']['sharpe']:+.3f})")


if __name__ == "__main__":
    main()
