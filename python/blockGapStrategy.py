"""
blockGapStrategy.py
-------------------
SPY block-order gap trading strategy backtest.

Strategy rules:
  - Signal day T: detect block orders from block_events/block_outcomes CSVs
  - Execution: open of T+1 (next trading day) - no look-ahead bias
  - Above-market block on day T -> go LONG SPY at T+1 open
  - Below-market block on day T -> EXIT to cash at T+1 open (if long)
  - No shorting. Only long or cash.
  - Multiple signals on same day: any below_market = exit signal (below is rare/strong).
    If all above_market, stay/go long.
  - Hold until opposite signal fires.
  - Transaction cost: 0.01% per side (2 basis points round-trip)
  - Benchmark: buy-and-hold SPY over the same period

Usage:
  python blockGapStrategy.py
  python blockGapStrategy.py --from 2022-01-01   # if more backfill data available
"""

import argparse
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

EVENTS_CSV   = os.path.expanduser("~/discordBot/outputs/research/block_events.csv")
OUTCOMES_CSV = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")
PRICES_CSV   = os.path.expanduser("~/discordBot/outputs/markets/cache/spy_vwap_daily.csv")

TC = 0.0001   # 0.01% per side


# ── load and prep ──────────────────────────────────────────────────────────────

def load_prices(from_date=None, to_date=None) -> pd.DataFrame:
    df = pd.read_csv(PRICES_CSV, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df[["date", "open_price", "close_price"]].dropna()
    if from_date:
        df = df[df["date"] >= pd.Timestamp(from_date)]
    if to_date:
        df = df[df["date"] <= pd.Timestamp(to_date)]
    return df.reset_index(drop=True)


def load_signals() -> pd.DataFrame:
    """
    Merge events + outcomes, collapse to one signal per day.
    Returns DataFrame with columns: trade_date, signal (long/exit)
    Any below_market block on a day -> exit signal (takes priority).
    """
    events   = pd.read_csv(EVENTS_CSV, parse_dates=["trade_date"])
    outcomes = pd.read_csv(OUTCOMES_CSV, parse_dates=["trade_date"])

    # Merge on event_idx
    merged = events.merge(
        outcomes[["event_idx", "direction"]],
        left_index=True, right_on="event_idx", how="left"
    )

    # Daily signal: below_market on any event = exit; otherwise long
    def day_signal(group):
        if (group["direction"] == "below_market").any():
            return "exit"
        return "long"

    daily = (merged.groupby("trade_date")
             .apply(day_signal)
             .reset_index()
             .rename(columns={0: "signal"}))
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    return daily.sort_values("trade_date").reset_index(drop=True)


# ── backtest engine ────────────────────────────────────────────────────────────

def run_backtest(prices: pd.DataFrame, signals: pd.DataFrame) -> dict:
    """
    Walk forward day by day. Signal on day T executes at open of T+1.
    Returns dict with trades list, equity curve, and summary stats.
    """
    # Build a lookup: date -> signal
    sig_map = dict(zip(signals["trade_date"], signals["signal"]))

    # Align to price dates
    prices = prices.copy().reset_index(drop=True)
    price_dates = set(prices["date"])

    # State
    position    = "cash"        # cash or long
    entry_price = None
    entry_date  = None
    pending_sig = None          # signal queued for next open

    trades      = []
    equity      = []
    cash_value  = 1.0           # normalized starting equity

    for i, row in prices.iterrows():
        today      = row["date"]
        open_px    = row["open_price"]
        close_px   = row["close_price"]

        # --- Execute pending signal at today's open ---
        if pending_sig == "long" and position == "cash":
            # Enter long
            entry_price = open_px * (1 + TC)   # buy with slippage
            entry_date  = today
            position    = "long"
            pending_sig = None

        elif pending_sig == "exit" and position == "long":
            # Exit long
            exit_price = open_px * (1 - TC)
            ret        = (exit_price / entry_price) - 1.0
            cash_value *= (1 + ret)
            trades.append({
                "entry_date":  entry_date,
                "exit_date":   today,
                "entry_price": entry_price,
                "exit_price":  exit_price,
                "return_pct":  ret * 100,
                "hold_days":   (today - entry_date).days,
            })
            entry_price = None
            entry_date  = None
            position    = "cash"
            pending_sig = None

        elif pending_sig in ("long", "exit"):
            # Already in right state or redundant signal
            pending_sig = None

        # --- Mark-to-market equity for today ---
        if position == "long" and entry_price:
            # equity = cash_value * (today's close / entry price, net of entry TC)
            raw_entry = entry_price / (1 + TC)   # actual open price paid before TC
            mtm_equity = cash_value * (close_px / raw_entry)
            equity.append({
                "date":   today,
                "equity": mtm_equity,
                "in_mkt": 1,
            })
        else:
            equity.append({
                "date":   today,
                "equity": cash_value,
                "in_mkt": 0,
            })

        # --- Queue tomorrow's signal ---
        sig = sig_map.get(today)
        if sig == "long":
            if position == "cash":
                pending_sig = "long"
        elif sig == "exit":
            if position == "long":
                pending_sig = "exit"

    # Close any open position at last close
    if position == "long" and entry_price:
        last_close = prices["close_price"].iloc[-1]
        exit_price = last_close * (1 - TC)
        ret        = (exit_price / entry_price) - 1.0
        cash_value *= (1 + ret)
        trades.append({
            "entry_date":  entry_date,
            "exit_date":   prices["date"].iloc[-1],
            "entry_price": entry_price,
            "exit_price":  exit_price,
            "return_pct":  ret * 100,
            "hold_days":   (prices["date"].iloc[-1] - entry_date).days,
            "open_trade":  True,
        })
        # Update final equity point
        equity[-1]["equity"] = cash_value
        equity[-1]["in_mkt"] = 0

    eq_df = pd.DataFrame(equity).set_index("date")

    return {
        "trades":         pd.DataFrame(trades),
        "equity":         eq_df,
        "final_equity":   eq_df["equity"].iloc[-1],
        "prices":         prices,
    }


# ── performance metrics ────────────────────────────────────────────────────────

def calc_metrics(equity_series: pd.Series, prices: pd.DataFrame, label: str) -> dict:
    eq = equity_series.values
    rets = np.diff(eq) / eq[:-1]

    total_ret   = (eq[-1] / eq[0]) - 1.0
    n_days      = len(eq)
    n_years     = n_days / 252
    cagr        = (eq[-1] / eq[0]) ** (1 / n_years) - 1

    vol         = rets.std() * np.sqrt(252)
    sharpe      = (rets.mean() * 252) / (rets.std() * np.sqrt(252)) if rets.std() > 0 else 0

    # Max drawdown
    running_max = np.maximum.accumulate(eq)
    dd          = (eq - running_max) / running_max
    max_dd      = dd.min()

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    return {
        "label":     label,
        "total_ret": total_ret * 100,
        "cagr":      cagr * 100,
        "vol":       vol * 100,
        "sharpe":    sharpe,
        "max_dd":    max_dd * 100,
        "calmar":    calmar,
        "n_days":    n_days,
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    rets = trades["return_pct"]
    wins = (rets > 0).sum()
    losses = (rets <= 0).sum()
    return {
        "n_trades":     len(trades),
        "win_rate":     wins / len(trades) * 100,
        "avg_win":      rets[rets > 0].mean() if wins > 0 else 0,
        "avg_loss":     rets[rets <= 0].mean() if losses > 0 else 0,
        "avg_hold_days": trades["hold_days"].mean(),
        "med_hold_days": trades["hold_days"].median(),
        "best_trade":   rets.max(),
        "worst_trade":  rets.min(),
        "profit_factor": (rets[rets > 0].sum() / abs(rets[rets <= 0].sum())
                          if losses > 0 and rets[rets <= 0].sum() != 0 else float("inf")),
    }


def time_in_market(equity_df: pd.DataFrame) -> float:
    return equity_df["in_mkt"].mean() * 100


# ── benchmark ──────────────────────────────────────────────────────────────────

def build_bnh(prices: pd.DataFrame) -> pd.Series:
    """Buy-and-hold normalized equity curve."""
    closes = prices["close_price"].values
    return pd.Series(closes / closes[0], index=prices["date"])


# ── report ─────────────────────────────────────────────────────────────────────

def print_report(result: dict, signals: pd.DataFrame, from_date=None):
    trades  = result["trades"]
    eq_df   = result["equity"]
    prices  = result["prices"]

    strat_eq = eq_df["equity"]
    bnh_eq   = build_bnh(prices)

    strat_m  = calc_metrics(strat_eq, prices, "Block Gap Strategy")
    bnh_m    = calc_metrics(bnh_eq,   prices, "Buy-and-Hold SPY")
    tstats   = trade_stats(trades)
    tim      = time_in_market(eq_df)

    date_range = f"{prices['date'].iloc[0].date()} to {prices['date'].iloc[-1].date()}"

    sep = "=" * 68

    print(sep)
    print("  SPY BLOCK GAP STRATEGY - BACKTEST RESULTS")
    print(f"  Period: {date_range}  ({strat_m['n_days']} trading days)")
    print(sep)
    print()

    # Signal summary
    n_long = (signals["signal"] == "long").sum()
    n_exit = (signals["signal"] == "exit").sum()
    print(f"  Signals: {len(signals)} signal days  |  {n_long} long  |  {n_exit} exit")
    print(f"  Time in market: {tim:.1f}%")
    print()

    # Performance table
    print(f"  {'Metric':<22}  {'Strategy':>12}  {'Buy-and-Hold':>12}")
    print(f"  {'-'*22}  {'-'*12}  {'-'*12}")
    metrics = [
        ("Total Return",    "total_ret", "%"),
        ("CAGR",            "cagr",      "%"),
        ("Volatility (ann)","vol",       "%"),
        ("Sharpe Ratio",    "sharpe",    "x"),
        ("Max Drawdown",    "max_dd",    "%"),
        ("Calmar Ratio",    "calmar",    "x"),
    ]
    for label, key, unit in metrics:
        sv = strat_m[key]
        bv = bnh_m[key]
        if unit == "%":
            print(f"  {label:<22}  {sv:>+11.2f}%  {bv:>+11.2f}%")
        else:
            print(f"  {label:<22}  {sv:>12.2f}  {bv:>12.2f}")
    print()

    # Trade stats
    if not trades.empty:
        print(f"  Trade Statistics")
        print(f"  {'-'*44}")
        print(f"  Trades completed        : {tstats['n_trades']}")
        print(f"  Win rate                : {tstats['win_rate']:.1f}%")
        print(f"  Avg win                 : +{tstats['avg_win']:.2f}%")
        print(f"  Avg loss                : {tstats['avg_loss']:.2f}%")
        print(f"  Profit factor           : {tstats['profit_factor']:.2f}x")
        print(f"  Avg hold (calendar days): {tstats['avg_hold_days']:.1f}")
        print(f"  Median hold             : {tstats['med_hold_days']:.0f} days")
        print(f"  Best trade              : +{tstats['best_trade']:.2f}%")
        print(f"  Worst trade             : {tstats['worst_trade']:.2f}%")
        print()

    # Individual trades
    print(f"  Trade Log")
    print(f"  {'-'*68}")
    print(f"  {'Entry':>10}  {'Exit':>10}  {'Entry$':>8}  {'Exit$':>8}  {'Ret%':>7}  {'Days':>5}  {'Status'}")
    print(f"  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*8}")
    for _, t in trades.iterrows():
        status = "OPEN" if t.get("open_trade") is True else "closed"
        sign   = "+" if t["return_pct"] >= 0 else ""
        print(f"  {str(t['entry_date'].date()):>10}  {str(t['exit_date'].date()):>10}"
              f"  {t['entry_price']:>8.2f}  {t['exit_price']:>8.2f}"
              f"  {sign}{t['return_pct']:>6.2f}%  {int(t['hold_days']):>5}  {status}")
    print()

    # Calendar year breakdown
    print(f"  Annual Returns")
    print(f"  {'-'*44}")
    eq_df_merged = eq_df.copy()
    eq_df_merged["year"] = eq_df_merged.index.year
    bnh_aligned = bnh_eq.reindex(eq_df.index)

    for yr, grp in eq_df_merged.groupby("year"):
        eq_start = grp["equity"].iloc[0]
        eq_end   = grp["equity"].iloc[-1]
        s_ret    = (eq_end / eq_start - 1) * 100

        bnh_s    = bnh_aligned.loc[grp.index[0]]
        bnh_e    = bnh_aligned.loc[grp.index[-1]]
        b_ret    = (bnh_e / bnh_s - 1) * 100

        tim_yr   = grp["in_mkt"].mean() * 100
        print(f"  {yr}:  Strategy {s_ret:>+7.2f}%   SPY {b_ret:>+7.2f}%   in-mkt {tim_yr:.0f}%")

    print()
    print(sep)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default=None,
                        help="Start date YYYY-MM-DD (default: first signal date)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default: latest price)")
    args = parser.parse_args()

    if not os.path.exists(EVENTS_CSV):
        print("ERROR: block_events.csv not found"); sys.exit(1)
    if not os.path.exists(OUTCOMES_CSV):
        print("ERROR: block_outcomes.csv not found"); sys.exit(1)
    if not os.path.exists(PRICES_CSV):
        print("ERROR: spy_vwap_daily.csv not found"); sys.exit(1)

    signals = load_signals()
    prices  = load_prices(args.from_date, args.to_date)

    # Trim prices to start one day before first signal so we have an open to trade on
    first_sig = signals["trade_date"].min()
    prices = prices[prices["date"] >= first_sig].reset_index(drop=True)

    result = run_backtest(prices, signals)
    print_report(result, signals, args.from_date)


if __name__ == "__main__":
    main()
