#!/usr/bin/env python3
"""
spyMetaLabelTrain.py
=======================
Meta-labeling sizing overlay (Oct'26) -- the supervised-learning answer to
the same question the RL-wrap spike tried to answer ("should I scale this
day's position up/down given confidence + recent performance?"), built
after full-replace RL (collapsed to flat / destabilized) and RL-wrap
(marginal, noisy, high-variance multiplier) both failed to clear the bar.

Approach (standard meta-labeling, adapted for a 1-day-hold strategy
instead of the classic multi-day triple-barrier framing, since this book
rebalances daily and there's no multi-day holding period to put barriers
on):
  1. PRIMARY bet = the existing fixed-50%-Kelly rule's gated position
     (pos_base from spyVolTargetWFCV.apply_gates). This reuses the
     production rule's own gates (kill switch / chop-VIX gate / bear
     halving) UNCHANGED -- same safety-rail-preservation principle as the
     RL-wrap spike, but now the meta-model only ever scales days the rule
     already allows a position on.
  2. LABEL = did that day's bet, AS ACTUALLY SIZED AND COSTED (i.e. using
     spyVolTargetWFCV.backtest_positions' net daily_ret, which already
     includes transaction cost on turnover), come out net positive. Only
     defined/trained on days the gate allowed a nonzero position --
     gated-zero days are untouched by construction (meta_mult is
     irrelevant when pos_base==0).
  3. META-MODEL = a SEPARATE small logistic regression (kept simple
     deliberately -- "wins/losses" samples are far fewer than raw days,
     so a complex model would overfit) trained to predict P(win) from
     confidence (|p-0.5|), regime, vix/gex context, realized AND
     GARCH-forecast vol (Oct'26 spike output), and a CAUSAL rolling
     win-rate of the primary rule's own last 10/21 bets (shifted so no
     leakage -- the agent/model only ever sees PRIOR bet outcomes).
  4. SIZING: final_position = pos_base * clip(meta_prob / train_base_rate,
     0, 2) -- multiplier is 1.0x when meta_prob equals the TRAINING
     fold's empirical base win rate (i.e. "no information" -> defer to
     the existing rule), scales up when meta-model is more confident than
     base rate, down when less. Same [0,2] multiplier range as the
     RL-wrap spike for direct comparability.

Walk-forward discipline: meta-model trained ONLY on pooled OOS days from
PRIOR WFCV folds (same fold scheme as every other spike this month):
  Eval A: meta-train on folds 1+2 -> eval OOS on fold 3
  Eval B: meta-train on folds 1+2+3 -> eval OOS on fold 4
This is zero-lookahead at both the primary-model level (already true of
oos['p'] from run_wfcv) AND the meta-model level.

Usage:
  venv/bin/python3 python/spyMetaLabelTrain.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from spyVolTargetWFCV import (run_wfcv, attach_realized_vol, base_kelly_fraction,
                               apply_gates, backtest_positions, ann_sharpe, max_drawdown,
                               ANNUAL)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
GARCH_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_garch_vol_forecast.csv")
OUT_PLOT_A = os.path.expanduser("~/discordBot/outputs/markets/spy_metalabel_foldA.png")
OUT_PLOT_B = os.path.expanduser("~/discordBot/outputs/markets/spy_metalabel_foldB.png")

REGIMES = ["bull", "bear", "chop"]
MULT_LOW, MULT_HIGH = 0.0, 2.0
SEED = 42


def build_meta_frame(oos, pos_base, bt_base_daily_ret, target_ann_vol):
    """Build meta-features + labels on the pooled OOS dataframe. Returns a
    new dataframe with added columns; rows are NOT filtered to bet-taken
    days here (that happens per-fold at train time) so rolling windows
    stay correctly ordered across the whole pooled series."""
    df = oos.reset_index(drop=True).copy()
    df["pos_base"] = pos_base
    df["daily_ret_base"] = bt_base_daily_ret
    df["bet_taken"] = df["pos_base"] > 0
    df["win"] = (df["daily_ret_base"] > 0).astype(int)

    df["confidence"] = np.abs(df["p"].values - 0.5)
    for r in REGIMES:
        df[f"regime_{r}"] = (df["regime"].values == r).astype(float)
    df["vix_level_n"] = df["vix_level"].fillna(df["vix_level"].median()) / 30.0
    df["vix_z21_f"] = df["vix_z21"].fillna(0.0)
    df["gex_sign_f"] = df["gex_sign"].fillna(1.0)
    df["realized_vol_21d_f"] = df["realized_vol_21d"].fillna(df["realized_vol_21d"].median())
    if "garch_vol_ann" in df.columns:
        df["garch_vol_ann_f"] = df["garch_vol_ann"].ffill().fillna(df["realized_vol_21d_f"])
    else:
        df["garch_vol_ann_f"] = df["realized_vol_21d_f"]
    df["vol_scalar_rv"] = np.clip(target_ann_vol / df["realized_vol_21d_f"].replace(0, np.nan), 0.1, 5.0).fillna(1.0)

    # CAUSAL rolling win-rate of the primary rule's own PRIOR bets, computed
    # only over bet-taken rows (chronological order), shifted by 1 so the
    # feature available "today" never includes today's own outcome.
    win_series = df["win"].where(df["bet_taken"])
    df["roll_winrate_10"] = win_series.rolling(10, min_periods=3).mean().shift(1)
    df["roll_winrate_21"] = win_series.rolling(21, min_periods=5).mean().shift(1)
    df["roll_winrate_10"] = df["roll_winrate_10"].ffill().fillna(0.5)
    df["roll_winrate_21"] = df["roll_winrate_21"].ffill().fillna(0.5)

    return df


META_FEATURES = [
    "confidence", "regime_bull", "regime_bear", "regime_chop",
    "vix_level_n", "vix_z21_f", "gex_sign_f",
    "realized_vol_21d_f", "garch_vol_ann_f", "vol_scalar_rv",
    "roll_winrate_10", "roll_winrate_21",
]


def train_meta_model(df_train):
    bet_rows = df_train[df_train["bet_taken"]].copy()
    if len(bet_rows) < 40 or bet_rows["win"].nunique() < 2:
        return None, None
    X = bet_rows[META_FEATURES].values
    y = bet_rows["win"].values
    model = Pipeline([("sc", StandardScaler()),
                       ("m", LogisticRegression(C=1.0, max_iter=500, random_state=SEED))])
    model.fit(X, y)
    base_rate = float(y.mean())
    return model, base_rate


def apply_meta_sizing(model, base_rate, df_eval):
    pos_base = df_eval["pos_base"].values.copy()
    final_pos = pos_base.copy()
    bet_mask = df_eval["bet_taken"].values
    mult = np.ones(len(df_eval))
    if model is not None and bet_mask.sum() > 0:
        X_eval = df_eval.loc[bet_mask, META_FEATURES].values
        meta_prob = model.predict_proba(X_eval)[:, 1]
        m = np.clip(meta_prob / base_rate, MULT_LOW, MULT_HIGH)
        mult[bet_mask] = m
        final_pos[bet_mask] = np.clip(pos_base[bet_mask] * m, 0.0, 1.0)
    return final_pos, mult


def summarize(daily_ret, label):
    sharpe = ann_sharpe(daily_ret)
    mdd = max_drawdown(daily_ret)
    total_ret = float(np.prod(1 + daily_ret) - 1)
    ann_ret = float(np.mean(daily_ret) * ANNUAL)
    print(f"  {label:<36} Sharpe {sharpe:+.3f}  MaxDD {mdd*100:+.2f}%  "
          f"TotalRet {total_ret*100:+.2f}%  AnnRet {ann_ret*100:+.2f}%")
    return dict(sharpe=sharpe, max_dd=mdd, total_return=total_ret, ann_return=ann_ret)


def paired_block_bootstrap_sharpe_delta(ret_a, ret_b, block_size=21, n_boot=2000, seed=SEED):
    """95% CI + P(delta>0) for ann_sharpe(ret_b) - ann_sharpe(ret_a) via
    moving-block bootstrap (same block indices applied to both series, so
    pairing/day-correspondence is preserved)."""
    rng = np.random.default_rng(seed)
    n = len(ret_a)
    block = min(block_size, n - 1) if n > 1 else 1
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        deltas[b] = ann_sharpe(ret_b[idx]) - ann_sharpe(ret_a[idx])
    return deltas


def run_fold_comparison(oos_meta, target_ann_vol, train_fold_ids, eval_fold_id, label, plot_path):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    fold_arr = oos_meta["fold"].values
    train_mask = np.isin(fold_arr, train_fold_ids)
    eval_mask = fold_arr == eval_fold_id

    df_train = oos_meta[train_mask].reset_index(drop=True)
    df_eval = oos_meta[eval_mask].reset_index(drop=True)
    n_bets_train = int(df_train["bet_taken"].sum())
    n_bets_eval = int(df_eval["bet_taken"].sum())
    print(f"  Meta-train days: {len(df_train)} ({n_bets_train} bets, folds {train_fold_ids})  "
          f"Eval days: {len(df_eval)} ({n_bets_eval} bets, fold {eval_fold_id})")

    model, base_rate = train_meta_model(df_train)
    if model is None:
        print("  SKIPPED: insufficient bet samples to train meta-model")
        return None
    print(f"  Training-fold base win rate: {base_rate:.3f}  (meta multiplier = 1.0x at this rate)")

    final_pos, mult = apply_meta_sizing(model, base_rate, df_eval)

    bt_meta = backtest_positions(df_eval, final_pos)
    bt_base = backtest_positions(df_eval, df_eval["pos_base"].values)

    raw_kelly_eval = base_kelly_fraction(df_eval["p"].values)
    vol_scalar_eval = np.clip(target_ann_vol / df_eval["realized_vol_21d_f"].values, 0.1, 5.0)
    vt_pos_eval = apply_gates(df_eval, raw_kelly_eval * vol_scalar_eval)
    bt_vt = backtest_positions(df_eval, vt_pos_eval)

    print()
    res_base = summarize(bt_base["daily_ret"], "Baseline (fixed 50% Kelly)")
    res_vt = summarize(bt_vt["daily_ret"], "Vol-targeted overlay")
    res_meta = summarize(bt_meta["daily_ret"], "Meta-labeled (confidence-scaled)")

    print(f"\n  Delta meta vs vol-targeted: Sharpe {res_meta['sharpe']-res_vt['sharpe']:+.3f}  "
          f"TotalRet {(res_meta['total_return']-res_vt['total_return'])*100:+.2f}pp")
    print(f"  Mean multiplier: {mult[df_eval['bet_taken'].values].mean():.3f}  "
          f"std={mult[df_eval['bet_taken'].values].std():.3f}")

    n_gated = int((df_eval['pos_base'].values == 0).sum())
    n_stay_zero = int((final_pos[df_eval['pos_base'].values == 0] == 0).sum())
    print(f"  Gate integrity: {n_stay_zero}/{n_gated} gated-zero days stayed zero "
          f"(must be {n_gated}/{n_gated})")

    deltas_vs_vt = paired_block_bootstrap_sharpe_delta(
        bt_vt["daily_ret"], bt_meta["daily_ret"])
    ci_lo, ci_hi = np.percentile(deltas_vs_vt, [2.5, 97.5])
    pct_pos = (deltas_vs_vt > 0).mean() * 100
    print(f"  Bootstrap Sharpe delta (meta - vol-tgt): mean={deltas_vs_vt.mean():+.3f}  "
          f"95%CI=[{ci_lo:+.3f},{ci_hi:+.3f}]  P(meta beats vol-tgt)={pct_pos:.1f}%")

    NAVY, GRID = "#02233F", "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    eq_base = np.cumprod(1 + bt_base["daily_ret"])
    eq_vt = np.cumprod(1 + bt_vt["daily_ret"])
    eq_meta = np.cumprod(1 + bt_meta["daily_ret"])
    eval_dates = df_eval["date"].values
    ax.plot(eval_dates, eq_base, color="#5DADE2", lw=1.6,
            label=f"Fixed 50% Kelly (Sharpe {res_base['sharpe']:+.2f})")
    ax.plot(eval_dates, eq_vt, color="#F5B041", lw=1.6,
            label=f"Vol-targeted (Sharpe {res_vt['sharpe']:+.2f})")
    ax.plot(eval_dates, eq_meta, color="#2E9E4E", lw=1.8,
            label=f"Meta-labeled sizing (Sharpe {res_meta['sharpe']:+.2f})")
    ax.set_title(f"{label}\nMeta-labeled confidence sizing vs hand-coded rules, OOS eval fold",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative return (x)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"meta-train folds {train_fold_ids} ({n_bets_train} bets) -> eval fold "
              f"{eval_fold_id} ({n_bets_eval} bets) | base_win_rate={base_rate:.3f} | "
              f"mult range [0,2] | bootstrap P(beats vol-tgt)={pct_pos:.1f}%",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(plot_path, facecolor=NAVY, dpi=140)
    print(f"\n  Saved plot: {plot_path}")

    return dict(base=res_base, vt=res_vt, meta=res_meta, bootstrap_pct_pos=pct_pos,
                bootstrap_ci=(ci_lo, ci_hi))


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos = run_wfcv(df_raw)
    oos = attach_realized_vol(oos, df_raw)

    if os.path.exists(GARCH_PATH):
        garch = pd.read_csv(GARCH_PATH, parse_dates=["date"])
        oos = oos.merge(garch, on="date", how="left")
        print(f"Merged GARCH vol forecast: {oos['garch_vol_ann'].notna().sum()}/{len(oos)} rows matched")
    else:
        print("No GARCH forecast file found, falling back to realized vol for that feature")
        oos["garch_vol_ann"] = np.nan

    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"target_ann_vol (full-sample median): {target_ann_vol:.4f}")

    raw_kelly = base_kelly_fraction(oos["p"].values)
    pos_base = apply_gates(oos, raw_kelly)
    bt_base_full = backtest_positions(oos, pos_base)

    oos_meta = build_meta_frame(oos, pos_base, bt_base_full["daily_ret"], target_ann_vol)
    print(f"Overall bet rate: {oos_meta['bet_taken'].mean()*100:.1f}%  "
          f"overall win rate (bet days): {oos_meta.loc[oos_meta['bet_taken'],'win'].mean():.3f}")

    results = {}
    results["A"] = run_fold_comparison(oos_meta, target_ann_vol, train_fold_ids=[1, 2],
                                        eval_fold_id=3, label="Fold A: train[1,2] -> eval[3]",
                                        plot_path=OUT_PLOT_A)
    results["B"] = run_fold_comparison(oos_meta, target_ann_vol, train_fold_ids=[1, 2, 3],
                                        eval_fold_id=4, label="Fold B: train[1,2,3] -> eval[4]",
                                        plot_path=OUT_PLOT_B)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    for k, r in results.items():
        if r is None:
            continue
        print(f"  Eval {k}: baseline Sharpe {r['base']['sharpe']:+.3f}  "
              f"vol-tgt Sharpe {r['vt']['sharpe']:+.3f}  "
              f"meta Sharpe {r['meta']['sharpe']:+.3f}  "
              f"(meta - vol-tgt: {r['meta']['sharpe']-r['vt']['sharpe']:+.3f}, "
              f"bootstrap P(beats)={r['bootstrap_pct_pos']:.1f}%)")


if __name__ == "__main__":
    main()
