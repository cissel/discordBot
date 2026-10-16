#!/usr/bin/env python3
"""
spyRlIntradayTrain.py
=======================
Trains PPO on 5-min-bar SPY sizing/exit (spyRlIntradayEnv.py) and compares
against the existing hand-coded daily baseline on the SAME calendar OOS
folds used throughout (spyVolTargetWFCV.py's 4 x 252-day expanding folds).

This directly tests the hypothesis from the Oct'26 daily-bar RL spike:
"PPO failed (flat collapse / unstable) because it only had 504-756 daily
transitions to learn from -- would ~1000x more transitions (5-min bars)
fix it?"

Per-bar RL rewards are aggregated to a DAILY return series (compound
within each trading day) so the resulting Sharpe is directly comparable,
apples-to-apples, to the daily hand-coded baseline/vol-targeted Sharpe
numbers already computed in spyRlSizingTrain.py.

Usage:
  venv/bin/python3 python/spyRlIntradayTrain.py
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
from spyRlIntradayEnv import build_day_index, SpyIntradayEnv, TX_COST, WINDOW

FEAT_PATH_DAILY = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
FEAT_PATH_5MIN = os.path.expanduser("~/discordBot/outputs/features/markets/spy_intraday_5min_features.csv")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy_rl")
os.makedirs(MODEL_DIR, exist_ok=True)

TOTAL_TIMESTEPS = 400_000
ANNUAL = 252
SEED = 42


def make_env(df, day_idx_subset, random_day, seed):
    def _f():
        env = SpyIntradayEnv(df, day_idx_subset, random_day=random_day, seed=seed)
        return Monitor(env)
    return _f


def train_ppo(df, day_idx_train, seed=SEED):
    env = DummyVecEnv([make_env(df, day_idx_train, random_day=True, seed=seed)])
    model = PPO("MlpPolicy", env, verbose=0, seed=seed,
                n_steps=512, batch_size=128, n_epochs=10,
                learning_rate=3e-4, gamma=0.99, policy_kwargs=dict(net_arch=[64, 64]))
    model.learn(total_timesteps=TOTAL_TIMESTEPS)
    return model


def eval_policy_daily(model, df, day_idx_eval):
    """Run the policy deterministically through each eval day sequentially.
    Returns (trading_dates, daily_net_ret) -- one compounded daily return
    per day, aggregated from that day's bar-level net rewards."""
    env = SpyIntradayEnv(df, day_idx_eval, random_day=False, seed=SEED)
    daily_rets = []
    dates = []
    for d in range(len(day_idx_eval)):
        env.set_day(d)
        obs, _ = env.reset()
        bar_rets = []
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            bar_rets.append(info["net_ret"])
            done = terminated or truncated
        day_ret = float(np.prod(1 + np.array(bar_rets)) - 1)
        daily_rets.append(day_ret)
        row0 = day_idx_eval[d][0]
        dates.append(df.iloc[row0]["trading_date"])
    return np.array(dates), np.array(daily_rets)


def summarize(daily_ret, label):
    sharpe = ann_sharpe(daily_ret)
    mdd = max_drawdown(daily_ret)
    total_ret = float(np.prod(1 + daily_ret) - 1)
    ann_ret = float(np.mean(daily_ret) * ANNUAL)
    print(f"  {label:<32} Sharpe {sharpe:+.3f}  MaxDD {mdd*100:+.2f}%  "
          f"TotalRet {total_ret*100:+.2f}%  AnnRet {ann_ret*100:+.2f}%")
    return dict(sharpe=sharpe, max_dd=mdd, total_return=total_ret, ann_return=ann_ret)


def run_fold(df5, day_index_by_fold, oos_daily, target_ann_vol,
             train_fold_ids, eval_fold_id, label, plot_path):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    train_days = []
    for fid in train_fold_ids:
        train_days += day_index_by_fold.get(fid, [])
    eval_days = day_index_by_fold.get(eval_fold_id, [])
    n_train_bars = sum(len(d) for d in train_days)
    n_eval_bars = sum(len(d) for d in eval_days)
    print(f"  RL-train days: {len(train_days)} ({n_train_bars} 5-min bars, folds {train_fold_ids})")
    print(f"  Eval days:     {len(eval_days)} ({n_eval_bars} 5-min bars, fold {eval_fold_id})")

    if len(train_days) < 30 or len(eval_days) < 10:
        print("  SKIPPED: insufficient days")
        return None

    model = train_ppo(df5, train_days)
    rl_dates, rl_daily_ret = eval_policy_daily(model, df5, eval_days)

    # Daily hand-coded baseline, restricted to same eval fold
    oos_eval = oos_daily[oos_daily["fold"] == eval_fold_id].reset_index(drop=True)
    raw_kelly = base_kelly_fraction(oos_eval["p"].values)
    base_pos = apply_gates(oos_eval, raw_kelly)
    bt_base = backtest_positions(oos_eval, base_pos)

    vol_scalar = target_ann_vol / oos_eval["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    vt_pos = apply_gates(oos_eval, raw_kelly * vol_scalar)
    bt_vt = backtest_positions(oos_eval, vt_pos)

    print()
    res_base = summarize(bt_base["daily_ret"], "Baseline (fixed 50% Kelly, daily)")
    res_vt = summarize(bt_vt["daily_ret"], "Vol-targeted overlay (daily)")
    res_rl = summarize(rl_daily_ret, "RL (PPO, 5-min sizing/exit)")

    print(f"\n  Delta RL vs vol-targeted: Sharpe {res_rl['sharpe']-res_vt['sharpe']:+.3f}  "
          f"TotalRet {(res_rl['total_return']-res_vt['total_return'])*100:+.2f}pp")

    NAVY, GRID = "#02233F", "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    eq_base = np.cumprod(1 + bt_base["daily_ret"])
    eq_vt = np.cumprod(1 + bt_vt["daily_ret"])
    eq_rl = np.cumprod(1 + rl_daily_ret)
    n_plot = min(len(eq_base), len(eq_rl))
    ax.plot(oos_eval["date"].values[:n_plot], eq_base[:n_plot], color="#5DADE2", lw=1.6,
            label=f"Fixed 50% Kelly (Sharpe {res_base['sharpe']:+.2f})")
    ax.plot(oos_eval["date"].values[:n_plot], eq_vt[:n_plot], color="#F5B041", lw=1.6,
            label=f"Vol-targeted (Sharpe {res_vt['sharpe']:+.2f})")
    ax.plot(rl_dates[:n_plot], eq_rl[:n_plot], color="#2E9E4E", lw=1.8,
            label=f"RL 5-min sizing/exit (Sharpe {res_rl['sharpe']:+.2f})")
    ax.set_title(f"{label}\nRL (5-min bars) vs hand-coded daily sizing, OOS eval fold",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative return (x)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"RL-train folds {train_fold_ids} ({n_train_bars} 5-min bars) -> eval fold "
              f"{eval_fold_id} ({n_eval_bars} bars) | TX_COST={TX_COST} one-way | "
              f"PPO {TOTAL_TIMESTEPS} timesteps, WINDOW={WINDOW}",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(plot_path, facecolor=NAVY, dpi=140)
    print(f"\n  Saved plot: {plot_path}")

    model.save(os.path.join(MODEL_DIR, f"ppo_intraday_{label.split()[0].lower()}"))
    return dict(base=res_base, vt=res_vt, rl=res_rl)


def main():
    print("Loading daily OOS folds (for fold date boundaries + baseline)...")
    df_daily = pd.read_csv(FEAT_PATH_DAILY, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos_daily = run_wfcv(df_daily)
    oos_daily = attach_realized_vol(oos_daily, df_daily)

    spy_hist = df_daily[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())

    fold_date_map = {}
    for fid in sorted(oos_daily["fold"].unique()):
        fold_dates = set(oos_daily.loc[oos_daily["fold"] == fid, "date"].dt.date)
        fold_date_map[fid] = fold_dates

    print("Loading 5-min intraday features...")
    df5 = pd.read_csv(FEAT_PATH_5MIN, parse_dates=["date"])
    df5["trading_date"] = pd.to_datetime(df5["trading_date"]).dt.date
    day_index_all = build_day_index(df5)
    print(f"  {len(day_index_all)} usable trading days of 5-min bars")

    day_index_by_fold = {fid: [] for fid in fold_date_map}
    unmatched = 0
    for d in day_index_all:
        day_date = df5.iloc[d[0]]["trading_date"]
        matched = False
        for fid, dates in fold_date_map.items():
            if day_date in dates:
                day_index_by_fold[fid].append(d)
                matched = True
                break
        if not matched:
            unmatched += 1
    for fid in sorted(day_index_by_fold):
        print(f"  fold {fid}: {len(day_index_by_fold[fid])} intraday trading days")
    print(f"  {unmatched} intraday days outside any daily OOS fold (pre-fold-1 training-only period)")

    results = {}
    results["A"] = run_fold(df5, day_index_by_fold, oos_daily, target_ann_vol,
                             train_fold_ids=[1, 2], eval_fold_id=3,
                             label="Fold A: train[1,2] -> eval[3]",
                             plot_path=os.path.expanduser(
                                 "~/discordBot/outputs/markets/spy_rl_intraday_foldA.png"))
    results["B"] = run_fold(df5, day_index_by_fold, oos_daily, target_ann_vol,
                             train_fold_ids=[1, 2, 3], eval_fold_id=4,
                             label="Fold B: train[1,2,3] -> eval[4]",
                             plot_path=os.path.expanduser(
                                 "~/discordBot/outputs/markets/spy_rl_intraday_foldB.png"))

    print(f"\n{'='*70}\nSUMMARY (intraday 5-min RL vs daily hand-coded baseline)\n{'='*70}")
    for k, r in results.items():
        if r is None:
            continue
        print(f"  Eval {k}: baseline Sharpe {r['base']['sharpe']:+.3f}  "
              f"vol-tgt Sharpe {r['vt']['sharpe']:+.3f}  "
              f"RL(5min) Sharpe {r['rl']['sharpe']:+.3f}  "
              f"(RL - vol-tgt: {r['rl']['sharpe']-r['vt']['sharpe']:+.3f})")


if __name__ == "__main__":
    main()
