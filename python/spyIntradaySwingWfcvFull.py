#!/usr/bin/env python3
"""
spyIntradaySwingWfcvFull.py
==============================
Extends the intraday swing-reversal backtest (spyIntradaySwingTrain.py) to
ALL 4 daily-WFCV eval folds instead of just 2 (Oct'26, James's request:
tighten the CI on the 0.4% swing-threshold result by adding more OOS
evidence). Expanding-window training for every eval fold -- eval fold N
trains on ALL days strictly before fold N's start, including the
pre-fold-1 history (2021-01-04 onward, ~444 days) that the original A/B
split never used for training. This roughly doubles the OOS days behind
the bootstrap CI (503 -> ~1007 days) without changing the model,
threshold, or backtest mechanics at all -- purely more evidence on the
SAME methodology that produced the 0.4% result.

Usage:
  venv/bin/python3 python/spyIntradaySwingWfcvFull.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

import spyIntradaySwingTrain as m
from spyVolTargetWFCV import run_wfcv, ann_sharpe as _unused_ann_sharpe

# Label set: pass a suffix as argv[1] ("" = original 0.15% zigzag labels, "_pct40bps",
# "_pct60bps"). Default stays pct40 for backward compatibility.
_SFX = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "_pct40bps"
SWING_LABEL_PATH = os.path.expanduser(
    f"~/discordBot/outputs/features/markets/spy_intraday_swing_labels{_SFX}.csv")
OUT_PATH = os.path.expanduser(f"~/discordBot/outputs/markets/spy_swing_wfcv_full_daily_pnl{_SFX or '_pct15bps'}.csv")
SEED = 42


def ann_sharpe(r):
    r = np.asarray(r)
    if len(r) == 0 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(252))


def block_bootstrap(r, block=21, n_boot=5000, seed=SEED):
    rng = np.random.default_rng(seed)
    n = len(r)
    if n <= block:
        block = max(1, n - 1)
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    out = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        resampled = np.concatenate([r[s:s + block] for s in starts])[:n]
        out[b] = ann_sharpe(resampled)
    return out


def main():
    print("Loading 5-min features + swing labels (0.4% threshold)...")
    feat = pd.read_csv(m.FEAT_PATH, parse_dates=["date"])
    feat["trading_date"] = pd.to_datetime(feat["trading_date"]).dt.date
    labels = pd.read_csv(SWING_LABEL_PATH, parse_dates=["date"])
    labels["trading_date"] = pd.to_datetime(labels["trading_date"]).dt.date
    df = feat.merge(labels[["trading_date", "bar_idx_in_day", "is_swing_high", "is_swing_low",
                             "swing_high_near", "swing_low_near"]],
                     on=["trading_date", "bar_idx_in_day"], how="inner")
    df = m.add_extra_causal_features(df)
    print(f"Merged: {len(df)} bars, {df['trading_date'].nunique()} days")

    df_daily = pd.read_csv(os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv"),
                            parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos_daily = run_wfcv(df_daily)
    fold_date_map = {fid: set(oos_daily.loc[oos_daily["fold"] == fid, "date"].dt.date)
                     for fid in sorted(oos_daily["fold"].unique())}
    df["fold"] = 0
    for fid, dates in fold_date_map.items():
        df.loc[df["trading_date"].isin(dates), "fold"] = fid
    print("Fold day counts:", df.groupby("fold")["trading_date"].nunique().to_dict())

    all_results = {}
    all_daily_pnl = []
    for eval_fold in [1, 2, 3, 4]:
        label = f"Fold {eval_fold}: expanding train (all days < fold {eval_fold} start)"
        print(f"\n{'='*70}\n{label}\n{'='*70}")
        df_train = df[df["fold"] < eval_fold].reset_index(drop=True)
        df_eval = df[df["fold"] == eval_fold].reset_index(drop=True)
        n_train_days = df_train["trading_date"].nunique()
        n_eval_days = df_eval["trading_date"].nunique()
        print(f"  Train bars: {len(df_train)} ({n_train_days} days)  "
              f"Eval bars: {len(df_eval)} ({n_eval_days} days)")

        if n_train_days < 60 or len(df_train) < 2000:
            print("  SKIPPED: insufficient training history")
            continue

        model_high = m.train_classifier(df_train, "swing_high_near")
        model_low = m.train_classifier(df_train, "swing_low_near")
        X_eval = df_eval[m.ALL_FEATURES].fillna(0.0).values
        prob_high = model_high.predict_proba(X_eval)[:, 1]
        prob_low = model_low.predict_proba(X_eval)[:, 1]

        from sklearn.metrics import roc_auc_score
        auc_high = roc_auc_score(df_eval["swing_high_near"].values, prob_high)
        auc_low = roc_auc_score(df_eval["swing_low_near"].values, prob_low)
        print(f"  Eval AUC swing_high: {auc_high:.4f}  swing_low: {auc_low:.4f}")

        trade_pnl, position, n_trades = m.backtest_reversal(df_eval, prob_high, prob_low)
        daily_pnl = m.daily_pnl_from_bar_pnl(df_eval, trade_pnl)
        sharpe = ann_sharpe(daily_pnl.values)
        print(f"  N trades: {n_trades}  Daily Sharpe: {sharpe:+.3f}  "
              f"Total P&L: {daily_pnl.sum()*100:+.2f}%")

        boot = block_bootstrap(daily_pnl.values)
        ci = np.percentile(boot, [2.5, 97.5])
        pct_pos = (boot > 0).mean() * 100
        print(f"  Bootstrap: mean={boot.mean():+.3f}  95%CI=[{ci[0]:+.3f},{ci[1]:+.3f}]  "
              f"P(Sharpe>0)={pct_pos:.1f}%")

        all_results[eval_fold] = dict(sharpe=sharpe, n_trades=n_trades,
                                       total_ret=float(daily_pnl.sum()),
                                       auc_high=auc_high, auc_low=auc_low,
                                       pct_pos=pct_pos, ci=ci)
        all_daily_pnl.append(daily_pnl)

    print(f"\n{'='*70}\nSUMMARY (all available folds)\n{'='*70}")
    for fid, r in all_results.items():
        print(f"  Fold {fid}: Sharpe={r['sharpe']:+.3f}  N_trades={r['n_trades']}  "
              f"TotalRet={r['total_ret']*100:+.2f}%  AUC(h/l)={r['auc_high']:.3f}/{r['auc_low']:.3f}  "
              f"Bootstrap P(>0)={r['pct_pos']:.1f}%")

    if all_daily_pnl:
        pooled = pd.concat(all_daily_pnl)
        pooled = pooled[~pooled.index.duplicated(keep="first")]
        pooled_sharpe = ann_sharpe(pooled.values)
        boot_pooled = block_bootstrap(pooled.values)
        ci_pooled = np.percentile(boot_pooled, [2.5, 97.5])
        pct_pos_pooled = (boot_pooled > 0).mean() * 100
        total_trades = sum(r["n_trades"] for r in all_results.values())
        print(f"\n  POOLED ({len(pooled)} days, {total_trades} total trades): "
              f"Sharpe={pooled_sharpe:+.3f}  "
              f"Bootstrap mean={boot_pooled.mean():+.3f}  95%CI=[{ci_pooled[0]:+.3f},{ci_pooled[1]:+.3f}]  "
              f"P(Sharpe>0)={pct_pos_pooled:.1f}%")
        pooled.to_csv(OUT_PATH)
        print(f"\n  Saved pooled daily P&L -> {OUT_PATH}")


if __name__ == "__main__":
    main()
