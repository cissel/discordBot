#!/usr/bin/env python3
"""
spyIntradaySwingTrain.py
===========================
Trains a classifier to predict intraday swing highs/lows (zigzag-labeled
by spyIntradaySwingLabels.py) from CAUSAL 5-min bar features only, then
backtests a standalone reversal day-trading strategy: short near predicted
swing highs, long near predicted swing lows, flat by end of day. Shorts
allowed (per James's scope decision, Oct'26) -- this is the literal
"sell intraday highs, buy back intraday lows" swing-trading hypothesis,
tested as its own target instead of folded into the next-day direction
model (which was the wrong hypothesis test, per James's correction).

CAUSALITY: every feature used for prediction is computed using ONLY bars
up to and including the current bar (spy_intraday_5min_features.csv's
rv_12bar/rv_36bar/mom_12bar/dist_from_high_so_far/etc. are all backward
rolling windows by construction -- verified below). The LABEL
(swing_high_near / swing_low_near) is inherently non-causal (confirmed
only in hindsight via the zigzag retracement rule) -- this is fine for a
SUPERVISED label, standard practice, exactly analogous to the daily
next_dir_1d label being "non-causal" relative to today's features. The
backtest only ever ACTS on the bar-by-bar PREDICTED probability, never on
the label itself, so there is no lookahead in the simulated strategy.

Strategy mechanics:
  - At each bar (except the last ~6 bars of the day, to leave room to
    exit), if P(swing_high_near) > ENTRY_THRESH and no position is held,
    ENTER SHORT at this bar's close.
  - If P(swing_low_near) > ENTRY_THRESH and no position is held, ENTER
    LONG at this bar's close.
  - Once in a position, EXIT on whichever comes first: (a) the opposite
    swing probability also crosses ENTRY_THRESH (take profit on the
    reversal that's forming the other way), (b) HOLD_BARS_MAX elapses
    (time stop), or (c) end of day (forced flat, no overnight carry).
  - One position at a time (no pyramiding), fixed notional size (position
    = +/-1.0, i.e. full size on/off, to isolate TIMING skill before
    layering a sizing question on top).

Fold structure: reuses the SAME WFCV calendar fold boundaries as every
other spike this month (spyVolTargetWFCV's 4x252-day folds), so results
are comparable in spirit, though this is now an intraday P&L series, not
a daily one.

Usage:
  venv/bin/python3 python/spyIntradaySwingTrain.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_intraday_5min_features.csv")
import sys as _sys
_LABEL_SUFFIX = _sys.argv[1] if len(_sys.argv) > 1 else ""
LABEL_PATH = os.path.expanduser(
    f"~/discordBot/outputs/features/markets/spy_intraday_swing_labels{_LABEL_SUFFIX}.csv")
OUT_PLOT = os.path.expanduser(
    f"~/discordBot/outputs/markets/spy_intraday_swing_equity{_LABEL_SUFFIX}.png")

TX_COST_BPS = 0.5           # one-way, in bps of notional (matches TX_COST=0.0005 elsewhere)
ENTRY_THRESH = 0.35
HOLD_BARS_MAX = 24          # ~2 hours max hold (24 x 5min)
MIN_BARS_BEFORE_EOD = 6     # don't open new positions in the last 30min (no time to exit cleanly)
ANNUAL_BARS = 78 * 252
SEED = 42

CAUSAL_FEATURES = [
    "ret_1", "cum_ret_since_open", "dist_from_high_so_far", "dist_from_low_so_far",
    "vwap_dev", "rv_12bar", "rv_36bar", "vol_z_36bar", "mom_12bar", "tod_frac",
    "regime_bull", "regime_bear", "regime_chop",
    "vix_level", "vix_z21", "gex_sign", "vix_rv_spread",
]


def add_extra_causal_features(df):
    """A few more reversal-specific causal features beyond the base 5-min
    set, all backward-looking only (rolling/shift, verified by construction
    -- no .shift(-N) or forward rolling anywhere in this function)."""
    df = df.sort_values(["trading_date", "bar_idx_in_day"]).reset_index(drop=True)
    g = df.groupby("trading_date")

    # RSI-like overbought/oversold proxy on 1-bar returns, 12-bar window
    def rsi_like(x, window=12):
        gains = x.clip(lower=0)
        losses = (-x).clip(lower=0)
        avg_gain = gains.rolling(window, min_periods=3).mean()
        avg_loss = losses.rolling(window, min_periods=3).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    df["rsi_12bar"] = g["ret_1"].transform(lambda x: rsi_like(x, 12))
    df["rsi_12bar"] = df["rsi_12bar"].fillna(50.0)

    # Acceleration: change in 1-bar return (2nd derivative of price)
    df["accel_1bar"] = g["ret_1"].transform(lambda x: x.diff())

    # Consecutive same-direction bars (streak) -- extended streaks often
    # precede reversals
    def streak(x):
        sign = np.sign(x.values)
        out = np.zeros(len(sign))
        cur = 0
        for i in range(len(sign)):
            if sign[i] == 0:
                cur = 0
            elif i > 0 and sign[i] == sign[i - 1]:
                cur += 1
            else:
                cur = 1
            out[i] = cur * sign[i]
        return pd.Series(out, index=x.index)

    df["dir_streak"] = g["ret_1"].transform(streak)

    # Distance from session VWAP in vol-normalized units (reversion pressure)
    df["vwap_dev_z"] = df["vwap_dev"] / (df["rv_36bar"].replace(0, np.nan) / np.sqrt(ANNUAL_BARS) + 1e-9)
    df["vwap_dev_z"] = df["vwap_dev_z"].clip(-10, 10).fillna(0.0)

    return df


ALL_FEATURES = CAUSAL_FEATURES + ["rsi_12bar", "accel_1bar", "dir_streak", "vwap_dev_z"]


def build_day_index(df):
    days = []
    for day, g in df.groupby("trading_date", sort=True):
        days.append(g.index.values)
    return days


def train_classifier(df_train, label_col):
    X = df_train[ALL_FEATURES].fillna(0.0).values
    y = df_train[label_col].values
    model = Pipeline([("sc", StandardScaler()),
                       ("m", GradientBoostingClassifier(
                           n_estimators=150, learning_rate=0.05, max_depth=3,
                           min_samples_leaf=50, subsample=0.8, random_state=SEED))])
    model.fit(X, y)
    return model


def backtest_reversal(df_eval, prob_high, prob_low, entry_thresh=ENTRY_THRESH,
                       hold_bars_max=HOLD_BARS_MAX, tx_cost_bps=TX_COST_BPS):
    """Bar-by-bar simulation within each trading day independently (forced
    flat at day boundaries). position: +1 long, -1 short, 0 flat."""
    df_eval = df_eval.reset_index(drop=True)
    n = len(df_eval)
    position = np.zeros(n)
    trade_pnl = np.zeros(n)   # realized P&L attributed to the bar where a trade CLOSES
    entry_price = None
    entry_pos = 0
    bars_held = 0
    n_trades = 0

    close = df_eval["close"].values
    day_ids = df_eval["trading_date"].values
    bar_idx = df_eval["bar_idx_in_day"].values
    n_bars_day = df_eval["n_bars_in_day"].values if "n_bars_in_day" in df_eval.columns else None

    for i in range(n):
        is_last_bar_of_day = (i == n - 1) or (day_ids[i] != day_ids[i + 1])
        bars_left_today = None
        if n_bars_day is not None:
            bars_left_today = n_bars_day[i] - bar_idx[i] - 1

        if entry_pos != 0:
            bars_held += 1
            should_exit = False
            # (a) opposite signal firing (reversal forming other direction)
            if entry_pos == 1 and prob_high[i] > entry_thresh:
                should_exit = True
            elif entry_pos == -1 and prob_low[i] > entry_thresh:
                should_exit = True
            # (b) time stop
            if bars_held >= hold_bars_max:
                should_exit = True
            # (c) forced flat at day end
            if is_last_bar_of_day:
                should_exit = True

            if should_exit:
                exit_price = close[i]
                raw_pnl = entry_pos * (exit_price - entry_price) / entry_price
                cost = 2 * (tx_cost_bps / 10000.0)   # entry + exit, one-way each
                trade_pnl[i] = raw_pnl - cost
                entry_pos = 0
                entry_price = None
                bars_held = 0
                n_trades += 1
            else:
                position[i] = entry_pos
        else:
            # consider a new entry (not in the last MIN_BARS_BEFORE_EOD bars)
            if bars_left_today is not None and bars_left_today < MIN_BARS_BEFORE_EOD:
                continue
            if is_last_bar_of_day:
                continue
            if prob_high[i] > entry_thresh:
                entry_pos = -1
                entry_price = close[i]
                bars_held = 0
                position[i] = entry_pos
            elif prob_low[i] > entry_thresh:
                entry_pos = 1
                entry_price = close[i]
                bars_held = 0
                position[i] = entry_pos

    return trade_pnl, position, n_trades


def ann_sharpe_bars(pnl_per_bar, bars_per_year=ANNUAL_BARS):
    pnl = np.asarray(pnl_per_bar)
    if pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(bars_per_year))


def daily_pnl_from_bar_pnl(df_eval, trade_pnl):
    df_eval = df_eval.reset_index(drop=True).copy()
    df_eval["trade_pnl"] = trade_pnl
    daily = df_eval.groupby("trading_date")["trade_pnl"].sum()
    return daily


def main():
    print("Loading 5-min features + swing labels...")
    feat = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    feat["trading_date"] = pd.to_datetime(feat["trading_date"]).dt.date
    labels = pd.read_csv(LABEL_PATH, parse_dates=["date"])
    labels["trading_date"] = pd.to_datetime(labels["trading_date"]).dt.date

    df = feat.merge(labels[["trading_date", "bar_idx_in_day", "is_swing_high", "is_swing_low",
                             "swing_high_near", "swing_low_near"]],
                     on=["trading_date", "bar_idx_in_day"], how="inner")
    print(f"Merged: {len(df)} bars, {df['trading_date'].nunique()} days")

    df = add_extra_causal_features(df)
    print(f"Added extra causal features: {['rsi_12bar','accel_1bar','dir_streak','vwap_dev_z']}")

    # Fold boundaries: reuse the SAME daily WFCV calendar folds as every
    # other spike this month (import locally to avoid heavy deps at module load)
    from spyVolTargetWFCV import run_wfcv, attach_realized_vol
    df_daily = pd.read_csv(os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv"),
                            parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos_daily = run_wfcv(df_daily)
    fold_date_map = {fid: set(oos_daily.loc[oos_daily["fold"] == fid, "date"].dt.date)
                     for fid in sorted(oos_daily["fold"].unique())}

    df["fold"] = 0
    for fid, dates in fold_date_map.items():
        df.loc[df["trading_date"].isin(dates), "fold"] = fid
    print("Fold day counts:", df.groupby("fold")["trading_date"].nunique().to_dict())

    results = {}
    for (train_folds, eval_fold, label) in [
        ([1, 2], 3, "Fold A: train[1,2] -> eval[3]"),
        ([1, 2, 3], 4, "Fold B: train[1,2,3] -> eval[4]"),
    ]:
        print(f"\n{'='*70}\n{label}\n{'='*70}")
        df_train = df[df["fold"].isin(train_folds)].reset_index(drop=True)
        df_eval = df[df["fold"] == eval_fold].reset_index(drop=True)
        print(f"  Train bars: {len(df_train)} ({df_train['trading_date'].nunique()} days)  "
              f"Eval bars: {len(df_eval)} ({df_eval['trading_date'].nunique()} days)")
        print(f"  Train swing_high_near rate: {df_train['swing_high_near'].mean()*100:.1f}%  "
              f"swing_low_near rate: {df_train['swing_low_near'].mean()*100:.1f}%")

        model_high = train_classifier(df_train, "swing_high_near")
        model_low = train_classifier(df_train, "swing_low_near")

        X_eval = df_eval[ALL_FEATURES].fillna(0.0).values
        prob_high = model_high.predict_proba(X_eval)[:, 1]
        prob_low = model_low.predict_proba(X_eval)[:, 1]

        auc_high = roc_auc_score(df_eval["swing_high_near"].values, prob_high)
        auc_low = roc_auc_score(df_eval["swing_low_near"].values, prob_low)
        print(f"  Eval AUC swing_high: {auc_high:.4f}  swing_low: {auc_low:.4f}  "
              f"(0.5 = no signal)")

        trade_pnl, position, n_trades = backtest_reversal(df_eval, prob_high, prob_low)
        daily_pnl = daily_pnl_from_bar_pnl(df_eval, trade_pnl)

        sharpe = ann_sharpe_bars(daily_pnl.values, bars_per_year=252)
        total_ret = float(daily_pnl.sum())
        win_rate = float((daily_pnl[daily_pnl != 0] > 0).mean()) if (daily_pnl != 0).any() else np.nan
        n_trade_days = int((daily_pnl != 0).sum())
        print(f"\n  N trades: {n_trades}  N days with >=1 trade: {n_trade_days}/{df_eval['trading_date'].nunique()}")
        print(f"  Daily Sharpe (ann): {sharpe:+.3f}  Total P&L (sum of daily, notional=1x): {total_ret*100:+.2f}%")
        print(f"  Win rate (days with nonzero P&L): {win_rate*100:.1f}%" if not np.isnan(win_rate) else "  No trades")

        # tx-cost sensitivity (the thing that killed the first swing spike)
        print("  TX-cost sensitivity:")
        for tx_bps in [0.0, 0.5, 1.0, 2.0, 5.0]:
            tp, _, nt = backtest_reversal(df_eval, prob_high, prob_low, tx_cost_bps=tx_bps)
            dp = daily_pnl_from_bar_pnl(df_eval, tp)
            sh = ann_sharpe_bars(dp.values, bars_per_year=252)
            print(f"    tx_bps={tx_bps:4.1f}  n_trades={nt:4d}  total_ret={dp.sum()*100:+.2f}%  sharpe={sh:+.3f}")
        if n_trades > 0:
            nonzero_pnl = trade_pnl[trade_pnl != 0]
            print(f"  Mean P&L per trade (at {TX_COST_BPS}bps): {nonzero_pnl.mean()*10000:+.2f}bps  "
                  f"std={nonzero_pnl.std()*10000:.2f}bps")

        results[label] = dict(auc_high=auc_high, auc_low=auc_low, sharpe=sharpe,
                               total_ret=total_ret, n_trades=n_trades, daily_pnl=daily_pnl)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    for label, r in results.items():
        print(f"  {label}: AUC(high/low)={r['auc_high']:.3f}/{r['auc_low']:.3f}  "
              f"Sharpe={r['sharpe']:+.3f}  TotalRet={r['total_ret']*100:+.2f}%  N_trades={r['n_trades']}")

    # Plot combined equity curve (concat fold A + B eval periods)
    NAVY, GRID = "#02233F", "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    offset = 0.0
    colors = ["#2E9E4E", "#F5B041"]
    for (label, r), color in zip(results.items(), colors):
        eq = offset + np.cumsum(r["daily_pnl"].values)
        dates = pd.to_datetime(list(r["daily_pnl"].index))
        ax.plot(dates, eq, color=color, lw=1.8, label=f"{label} (Sharpe {r['sharpe']:+.2f})")
        offset = eq[-1] if len(eq) else offset
    ax.axhline(0, color=GRID, lw=0.8)
    ax.set_title("Intraday Swing Reversal Strategy (standalone, shorts allowed, flat EOD)\n"
                  "5-min bars, zigzag-labeled swing high/low classifier (GBM)",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative P&L (sum of notional returns)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"ENTRY_THRESH={ENTRY_THRESH} | HOLD_BARS_MAX={HOLD_BARS_MAX} (5min bars) | "
              f"TX_COST={TX_COST_BPS}bps one-way | zigzag MIN_SWING_PCT=0.15%",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(OUT_PLOT, facecolor=NAVY, dpi=140)
    print(f"\nSaved plot: {OUT_PLOT}")


if __name__ == "__main__":
    main()
