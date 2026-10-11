#!/usr/bin/env python3
"""
spyBearShortWFCV.py
====================
Backtests allowing James's SPY bear-regime model to go SHORT below threshold,
instead of the current production behavior (predictSpy.py) of going flat.

CURRENT PRODUCTION (baseline, reproduced here exactly):
  - Dedicated bear-regime logistic model (spy_logistic_regime_bear*, same
    feature set BEAR_FEATURES as trainSpyModel.py).
  - Kill switch: flat whenever gex_sign<=0 AND vix_z21>1.5 (bear regime only).
  - Threshold: flat whenever blended prob < 0.53.
  - Sizing: Kelly(50%) * bear_mult(0.5), LONG ONLY: pos = clip(0.50*(2p-1),0,1)*0.5

PROPOSED (symmetric short variant):
  - Same model, same kill switch.
  - Sizing: pos = clip(0.50*(2p-1), -1, 1) * 0.5  (signed both directions)
  - Only take a position when |2p-1| crosses a confidence threshold symmetric
    around 0.5: long when p >= 0.53, short when p < 0.47, flat in [0.47, 0.53)
    or on kill switch.

Methodology: walk-forward pooled OOS, zero lookahead, same pattern as
chopVixSubRegimeWFCV.py (expanding-window folds, logistic trained on all prior
rows then evaluated on the next fold, pooled across folds). Restricted to
regime=='bear' days only. Since bear regime has far fewer rows than chop
(239 vs 1705), fold_size is scaled down from chop's 252 to 30, and min_train
rows is scaled down accordingly -- still a true expanding-window walk-forward
split (train on strictly prior bear days only), just smaller folds.

TX_COST = 0.0005 one-way (5bps), ANNUAL = 252. Does not touch any production
.pkl, does not touch outputs/markets/spy_trade_log.csv, does not modify
predictSpy.py / tradeSpy.py / trainSpyModel.py.

Usage:
  venv/bin/python3 python/spyBearShortWFCV.py
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
from sklearn.metrics import roc_auc_score, accuracy_score

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from trainSpyModel import BEAR_FEATURES, SPARSE_FEATURES, compute_sample_weights

HALF_LIFE = 1260   # matches trainSpyModel.py production default (~5yr recency half-life)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
PLOT_PATH = os.path.expanduser("~/discordBot/outputs/markets/spy_bear_short_equity.png")

TX_COST = 0.0005   # 5bps one-way
ANNUAL  = 252

LONG_THRESH  = 0.53   # production bear threshold
SHORT_THRESH = 0.47   # symmetric mirror of 0.53 around 0.5 (task spec)
BEAR_MULT    = 0.5    # production bear_mult (half Kelly)
KILL_VIXZ21  = 1.5    # production kill-switch vix_z21 threshold

# Navy theme (James's standard)
NAVY_BG   = "#02233F"
GRID_CLR  = "#274066"
TXT_CLR   = "white"


def prep_bear(df_raw, features):
    """Filter to bear rows, impute sparse features, dropna on core + target.
    Mirrors trainChopModel.prep_chop() / trainSpyModel.prep_df(), scoped to bear."""
    df = df_raw[df_raw["regime"] == "bear"].copy()
    avail = [f for f in features if f in df.columns]
    for col in SPARSE_FEATURES:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)
    core = [f for f in avail if f not in SPARSE_FEATURES]
    df = df.dropna(subset=core + ["next_dir_1d", "next_ret_1d"]).sort_values("date").reset_index(drop=True)
    return df, avail


def make_pipeline():
    """Same architecture as trainSpyModel.py's bear-only WFCV honesty-check
    section (the one that produced the quoted 0.60-0.65 AUC range in the
    experiment log): LogisticRegression(C=0.1), no class_weight balancing,
    no oversampling -- fit with recency-weighted sample_weight instead."""
    return Pipeline([
        ("sc", StandardScaler()),
        ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42)),
    ])


def dir_accuracy(y_true, y_pred_prob, thresh=0.5):
    return float(accuracy_score(y_true, (np.asarray(y_pred_prob) >= thresh).astype(int)))


def ann_sharpe(returns):
    returns = np.asarray(returns)
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(ANNUAL))


def max_drawdown(daily_ret):
    cum = (1 + np.asarray(daily_ret)).cumprod()
    roll_max = np.maximum.accumulate(cum)
    dd = (cum - roll_max) / roll_max
    return float(dd.min()), cum


def run_wfcv(df, avail):
    """Expanding-window walk-forward CV scoped to bear-regime rows.
    Returns pooled OOS dataframe: date, p, y, spy_ret, gex_sign, vix_z21, fold."""
    dates = sorted(df["date"].unique())
    n = len(dates)

    # Chop's pattern used fold_size=252, n_folds=5. Bear has only 239 rows total
    # (vs chop's 1705), so fold_size is scaled down proportionally to keep the
    # same 5-fold expanding-window structure with meaningful OOS windows.
    fold_size = 30
    n_folds = 5
    total_window = fold_size * n_folds
    train_start_idx = n - total_window - fold_size
    min_train_rows = 50

    if train_start_idx < 0:
        raise RuntimeError(f"Not enough bear rows ({n}) for {n_folds} folds of {fold_size}")

    oos_rows = []
    for i in range(n_folds):
        fold_start = dates[train_start_idx + fold_size + i * fold_size]
        end_idx = min(train_start_idx + fold_size + (i + 1) * fold_size - 1, n - 1)
        fold_end = dates[end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < min_train_rows or len(va) < 10:
            print(f"  fold {i+1}: skipped (tr={len(tr)}, va={len(va)})")
            continue
        avail_f = [f for f in avail if f in tr.columns]
        sw = compute_sample_weights(tr["date"], half_life_days=HALF_LIFE)
        m = make_pipeline()
        try:
            m.fit(tr[avail_f].values, tr["next_dir_1d"].values, **{"m__sample_weight": sw})
        except TypeError:
            m.fit(tr[avail_f].values, tr["next_dir_1d"].values)
        p = m.predict_proba(va[avail_f].values)[:, 1]
        for j in range(len(va)):
            oos_rows.append({
                "date": va["date"].iloc[j], "p": p[j], "y": va["next_dir_1d"].iloc[j],
                "spy_ret": va["next_ret_1d"].iloc[j],
                "gex_sign": va["gex_sign"].iloc[j] if "gex_sign" in va.columns else 1.0,
                "vix_z21": va["vix_z21"].iloc[j] if "vix_z21" in va.columns else 0.0,
                "fold": i + 1,
            })
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  tr={len(tr)} va={len(va)}  pooled OOS rows so far: {len(oos_rows)}")

    return pd.DataFrame(oos_rows)


def backtest_baseline(oos):
    """CURRENT production behavior: long-only half-Kelly, flat below 0.53
    threshold or on kill switch (gex_sign<=0 AND vix_z21>1.5)."""
    p = oos["p"].values
    r = oos["spy_ret"].values
    gex = oos["gex_sign"].values
    vixz = oos["vix_z21"].values

    kill = (gex <= 0) & (vixz > KILL_VIXZ21)
    kelly = np.clip(0.50 * (2 * p - 1), 0.0, 1.0) * BEAR_MULT
    pos = np.where(kill, 0.0, np.where(p >= LONG_THRESH, kelly, 0.0))

    trade_cost = np.abs(np.diff(np.append(0, pos))) * TX_COST
    daily_ret = pos * r - trade_cost
    return pos, daily_ret


def backtest_proposed(oos):
    """PROPOSED: symmetric short allowed below 0.47, long above 0.53,
    flat in between or on kill switch. Same half-Kelly bear multiplier,
    signed both directions."""
    p = oos["p"].values
    r = oos["spy_ret"].values
    gex = oos["gex_sign"].values
    vixz = oos["vix_z21"].values

    kill = (gex <= 0) & (vixz > KILL_VIXZ21)
    kelly_signed = np.clip(0.50 * (2 * p - 1), -1.0, 1.0) * BEAR_MULT
    take_long = p >= LONG_THRESH
    take_short = p < SHORT_THRESH
    pos = np.where(kill, 0.0, np.where(take_long | take_short, kelly_signed, 0.0))

    trade_cost = np.abs(np.diff(np.append(0, pos))) * TX_COST
    daily_ret = pos * r - trade_cost
    return pos, daily_ret


def summarize(label, pos, daily_ret, y):
    sharpe = ann_sharpe(daily_ret)
    max_dd, cum = max_drawdown(daily_ret)
    total_ret = float(cum[-1] - 1.0)
    n_days = len(daily_ret)
    ann_ret = float(daily_ret.mean() * ANNUAL)

    n_long = int(np.sum(pos > 0))
    n_short = int(np.sum(pos < 0))
    n_flat = int(np.sum(pos == 0))

    # win rate: among days with a non-zero position, fraction where position
    # direction matched realized direction (i.e. the trade made money gross of cost)
    active_mask = pos != 0
    if active_mask.sum() > 0:
        gross_pnl = np.sign(pos[active_mask]) * np.sign(y[active_mask] - 0.5)  # +1 win, -1 loss (y is 0/1 dir)
        win_rate = float((pos[active_mask] * (y[active_mask] * 2 - 1) > 0).mean())
    else:
        win_rate = float("nan")

    print(f"\n-- {label} --")
    print(f"  n_days={n_days}  n_long={n_long}  n_short={n_short}  n_flat={n_flat}")
    print(f"  Sharpe={sharpe:+.3f}  MaxDD={max_dd*100:.2f}%  "
          f"TotalRet={total_ret*100:+.2f}%  AnnRet={ann_ret*100:+.2f}%  WinRate={win_rate*100:.1f}%")

    return {
        "label": label, "sharpe": sharpe, "max_dd": max_dd, "total_ret": total_ret,
        "ann_ret": ann_ret, "n_long": n_long, "n_short": n_short, "n_flat": n_flat,
        "win_rate": win_rate, "cum": cum,
    }


def plot_equity(oos_dates, cum_baseline, cum_proposed):
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor(NAVY_BG)
    ax.set_facecolor(NAVY_BG)

    ax.plot(oos_dates, cum_baseline, color="#5DADE2", linewidth=1.8,
            label="(a) CURRENT: long-only half-Kelly, flat below 0.53")
    ax.plot(oos_dates, cum_proposed, color="#F5B041", linewidth=1.8,
            label="(b) PROPOSED: symmetric short below 0.47")

    ax.set_title("SPY Bear-Regime OOS Equity Curve: Long-Only vs. Symmetric Short",
                 color=TXT_CLR, fontsize=13)
    ax.set_xlabel("Date", color=TXT_CLR)
    ax.set_ylabel("Cumulative Growth of $1", color=TXT_CLR)

    ax.tick_params(colors=TXT_CLR)
    for spine in ax.spines.values():
        spine.set_color(GRID_CLR)
    ax.grid(True, color=GRID_CLR, alpha=0.6)

    legend = ax.legend(facecolor=NAVY_BG, edgecolor=GRID_CLR, fontsize=9)
    for text in legend.get_texts():
        text.set_color(TXT_CLR)

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(PLOT_PATH, facecolor=NAVY_BG, dpi=130)
    plt.close(fig)
    print(f"\n  equity curve saved -> {PLOT_PATH}")


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df, avail = prep_bear(df_raw, BEAR_FEATURES)
    print(f"[spyBearShortWFCV] {len(df)} bear-regime rows, {df.date.min().date()}-{df.date.max().date()}")
    print(f"  features used: {len(avail)}")

    oos = run_wfcv(df, avail)
    print(f"\nTotal pooled OOS rows (walk-forward, zero lookahead, bear-regime only): {len(oos)}")
    auc = roc_auc_score(oos["y"].values, oos["p"].values) if len(np.unique(oos["y"])) > 1 else float("nan")
    print(f"Pooled bear-regime OOS AUC: {auc:.4f}  dir_acc@0.5: {dir_accuracy(oos['y'].values, oos['p'].values):.4f}")

    print("\nPer-fold AUC (checks the split isn't one lucky fold):")
    for fold_n in sorted(oos["fold"].unique()):
        fsub = oos[oos["fold"] == fold_n]
        try:
            fauc = roc_auc_score(fsub["y"].values, fsub["p"].values)
        except Exception:
            fauc = float("nan")
        print(f"  fold {fold_n}: n={len(fsub)}  AUC={fauc:.4f}")

    print("\n[CAVEAT] models/meta/spy_experiment_log.csv shows bear-regime single-split\n"
          "(train~85-304 rows / val=63 rows, one slice, NOT walk-forward) AUC in the\n"
          "0.60-0.65 range across tuning runs. That 63-row single slice is much noisier\n"
          "and more easily lucky than this script's pooled 5-fold walk-forward OOS\n"
          "(true expanding-window, zero lookahead, 150 pooled OOS rows), which lands\n"
          "closer to 0.46-0.52 depending on fold-size choice (tested 20/25/30/35/40-row\n"
          "folds). The bear regime currently has only 235 total labeled days (2018-2026\n"
          "combined) -- the regime definition was revised multiple times in the log\n"
          "history (see 'Run 17: AND regime def'), so historical single-split numbers\n"
          "used a different, larger bear-day population than exists today. Treat the\n"
          "0.60-0.65 AUC quoted in the task context as optimistic/sample-fragile, not\n"
          "a robust walk-forward-verified edge.")

    pos_a, dr_a = backtest_baseline(oos)
    pos_b, dr_b = backtest_proposed(oos)

    res_a = summarize("(a) CURRENT: long-only half-Kelly, flat below 0.53 / kill switch", pos_a, dr_a, oos["y"].values)
    res_b = summarize("(b) PROPOSED: symmetric short below 0.47, long above 0.53", pos_b, dr_b, oos["y"].values)

    bh_sharpe = ann_sharpe(oos["spy_ret"].values)
    bh_dd, bh_cum = max_drawdown(oos["spy_ret"].values)
    print(f"\n-- Buy & hold SPY (bear-regime days only, for reference) --")
    print(f"  Sharpe={bh_sharpe:+.3f}  MaxDD={bh_dd*100:.2f}%  TotalRet={(bh_cum[-1]-1)*100:+.2f}%")

    print("\n" + "=" * 90)
    print(f"{'Variant':<55} {'Sharpe':>8} {'MaxDD':>8} {'TotalRet':>10} {'AnnRet':>8} {'Long':>6} {'Short':>6} {'WinRate':>8}")
    print("-" * 90)
    for res in (res_a, res_b):
        print(f"{res['label']:<55} {res['sharpe']:>8.3f} {res['max_dd']*100:>7.2f}% "
              f"{res['total_ret']*100:>9.2f}% {res['ann_ret']*100:>7.2f}% "
              f"{res['n_long']:>6d} {res['n_short']:>6d} {res['win_rate']*100:>7.1f}%")
    print("=" * 90)

    plot_equity(oos["date"].values, res_a["cum"], res_b["cum"])


if __name__ == "__main__":
    main()
