"""
blockBearExitStrategy.py
------------------------
Stay long SPY continuously. Exit to cash ONLY on a bear (below-market) block signal.
Re-enter when the bear gap fills (price touches block_price) or after 1mo stop.

Rules:
  - Default state: LONG SPY (enter at first available open)
  - Bear signal on day T (below-market block, largest by dollar_value if multiple):
      -> Exit to CASH at T+1 open
      -> Re-entry trigger: first trading day price reaches bear block_price
      -> Re-enter at open of the day AFTER the trigger fires
      -> If gap never fills within 1mo: re-enter at 1mo+1 open (stop)
  - While in cash: if a bull (above-market) block signal fires before bear gap fills,
      re-enter immediately at that signal's T+1 open (don't wait forever for bear gap)
  - Overlapping bear signals: if already in cash from prior bear, ignore new bear signals
    (already out). Track the FIRST unresolved bear block for re-entry target.

Outputs:
  outputs/research/bear_exit_trades.csv
  outputs/research/bear_exit_equity.csv
"""

import os, sys
import numpy as np
import pandas as pd

OUTCOMES_CSV = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")
PRICES_CSV   = os.path.expanduser("~/discordBot/outputs/markets/cache/spy_vwap_daily.csv")
TRADES_OUT   = os.path.expanduser("~/discordBot/outputs/research/bear_exit_trades.csv")
EQUITY_OUT   = os.path.expanduser("~/discordBot/outputs/research/bear_exit_equity.csv")

TC = 0.0001
HORIZONS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1mo": 21}


def nth_after(date_idx, ref, n):
    fut = date_idx[date_idx > ref]
    return fut[n - 1] if len(fut) >= n else (fut[-1] if len(fut) else None)


def load():
    outcomes = pd.read_csv(OUTCOMES_CSV, parse_dates=["trade_date"])
    prices   = pd.read_csv(PRICES_CSV,   parse_dates=["date"])
    prices   = prices[["date","open_price","close_price"]].dropna().sort_values("date").reset_index(drop=True)

    for h in HORIZONS:
        outcomes["reached_"+h] = outcomes["reached_"+h].astype(str).str.lower() == "true"

    return outcomes, prices


def build_bear_signals(outcomes, date_idx):
    """
    One bear signal per day: largest block by dollar_value.
    Returns dict: signal_date -> {block_price, exit_date, reentry_date, fill_h}
    """
    below = outcomes[outcomes["direction"] == "below_market"].copy()
    below = below.sort_values("dollar_value", ascending=False)
    bear  = below.drop_duplicates("trade_date", keep="first").copy()
    bear  = bear.sort_values("trade_date").reset_index(drop=True)

    signals = {}
    for _, row in bear.iterrows():
        sig_date  = row["trade_date"]
        exit_date = nth_after(date_idx, sig_date, 1)
        if exit_date is None:
            continue

        # Find re-entry: first horizon where reached = True -> re-enter day after fill
        reentry_date = None
        fill_h       = "never"
        for h, d in HORIZONS.items():
            if row["reached_" + h]:
                fill_day     = nth_after(date_idx, sig_date, d)
                reentry_date = nth_after(date_idx, fill_day, 1) if fill_day is not None else None
                fill_h       = h
                break

        if reentry_date is None:
            # Never filled - re-enter at 1mo+1 as stop
            fill_day     = nth_after(date_idx, sig_date, 21)
            reentry_date = nth_after(date_idx, fill_day, 1) if fill_day is not None else None
            fill_h       = "1mo_stop"

        signals[sig_date] = {
            "block_price":  float(row["block_price"]),
            "market_price": float(row["market_price"]),
            "exit_date":    exit_date,
            "reentry_date": reentry_date,
            "fill_h":       fill_h,
            "dollar_value": float(row["dollar_value"]),
            "deviation":    float(row["deviation"]),
        }

    return signals


def build_bull_signals(outcomes, date_idx):
    """
    Bull signals: above-market blocks, one per day (largest by dollar_value).
    Returns dict: signal_date -> entry_date (T+1)
    """
    above = outcomes[outcomes["direction"] == "above_market"].copy()
    above = above.sort_values("dollar_value", ascending=False)
    bull  = above.drop_duplicates("trade_date", keep="first").copy()

    signals = {}
    for _, row in bull.iterrows():
        entry_date = nth_after(date_idx, row["trade_date"], 1)
        if entry_date is not None:
            signals[row["trade_date"]] = entry_date
    return signals


def run_backtest(prices, outcomes):
    date_idx = pd.DatetimeIndex(prices["date"])
    open_map  = dict(zip(prices["date"], prices["open_price"]))
    close_map = dict(zip(prices["date"], prices["close_price"]))

    bear_sigs = build_bear_signals(outcomes, date_idx)
    bull_sigs = build_bull_signals(outcomes, date_idx)

    # State
    position      = "long"           # long or cash
    entry_price   = None             # price we entered long at (net of TC)
    entry_date    = None
    cash_entry    = None             # the bear signal that triggered our cash exit
    pending_entry = None             # (entry_date, reason) queued from bear gap fill

    trades        = []
    equity_rows   = []
    equity        = 1.0

    # Track all cash periods
    cash_periods  = []

    # Start: enter long at first open
    first_row  = prices.iloc[0]
    entry_open = first_row["open_price"] * (1 + TC)
    entry_price = entry_open
    entry_date  = first_row["date"]

    for i, row in prices.iterrows():
        today    = row["date"]
        open_px  = row["open_price"]
        close_px = row["close_price"]

        # ── Execute pending re-entry ───────────────────────────────────────────
        if pending_entry is not None and pending_entry[0] == today and position == "cash":
            entry_open_px = open_px * (1 + TC)
            entry_price   = entry_open_px
            entry_date    = today
            position      = "long"
            reason        = pending_entry[1]
            if cash_entry:
                cash_periods.append({
                    "exit_date":    cash_entry["exit_date"],
                    "reentry_date": today,
                    "fill_h":       cash_entry["fill_h"],
                    "block_price":  cash_entry["block_price"],
                    "reason":       reason,
                    "cash_days":    (today - cash_entry["exit_date"]).days,
                })
            cash_entry    = None
            pending_entry = None

        # ── Check for bear exit signal ─────────────────────────────────────────
        # Signal on today means exit tomorrow (already queued if we see sig_date == today)
        # We check yesterday's signals: if a bear signal fired on the PRIOR trading day,
        # execute exit at today's open.
        # Implementation: iterate signals and flag for execution on their exit_date
        if position == "long":
            for sig_date, sig in bear_sigs.items():
                if sig["exit_date"] == today:
                    # Exit long now
                    exit_px = open_px * (1 - TC)
                    ret     = (exit_px / entry_price) - 1.0
                    equity *= (1 + ret)
                    trades.append({
                        "type":        "exit",
                        "entry_date":  entry_date,
                        "exit_date":   today,
                        "entry_price": entry_price / (1 + TC),
                        "exit_price":  open_px,
                        "return_pct":  ret * 100,
                        "hold_days":   (today - entry_date).days,
                        "trigger":     f"bear block {sig_date.date()}",
                    })
                    position      = "cash"
                    entry_price   = None
                    entry_date    = None
                    cash_entry    = sig
                    cash_entry["signal_date"] = sig_date
                    # Queue re-entry
                    if sig["reentry_date"] is not None:
                        pending_entry = (sig["reentry_date"], "bear_gap_fill")
                    break   # only one exit per day

        # ── Check for early bull re-entry while in cash ────────────────────────
        # If a bull signal's entry_date == today and we're still in cash, re-enter now
        # (bull signal overrides waiting for bear gap fill)
        if position == "cash" and pending_entry is not None:
            for bull_sig_date, bull_entry_date in bull_sigs.items():
                if bull_entry_date == today and bull_entry_date < pending_entry[0]:
                    # Bull signal fires before scheduled bear re-entry - jump back in
                    pending_entry = (today, "bull_signal_override")
                    break

        # Execute override immediately (same day, at today's open)
        if (pending_entry is not None and pending_entry[0] == today
                and pending_entry[1] == "bull_signal_override" and position == "cash"):
            entry_open_px = open_px * (1 + TC)
            entry_price   = entry_open_px
            entry_date    = today
            position      = "long"
            if cash_entry:
                cash_periods.append({
                    "exit_date":    cash_entry["exit_date"],
                    "reentry_date": today,
                    "fill_h":       "bull_override",
                    "block_price":  cash_entry["block_price"],
                    "reason":       "bull_signal",
                    "cash_days":    (today - cash_entry["exit_date"]).days,
                })
            cash_entry    = None
            pending_entry = None

        # ── MTM equity ─────────────────────────────────────────────────────────
        if position == "long" and entry_price:
            raw_entry  = entry_price / (1 + TC)
            mtm_equity = equity * (close_px / raw_entry)
            equity_rows.append({"date": today, "equity": mtm_equity, "in_mkt": 1})
        else:
            equity_rows.append({"date": today, "equity": equity, "in_mkt": 0})

    # Close open long at last close
    if position == "long" and entry_price:
        last_close = prices["close_price"].iloc[-1]
        last_date  = prices["date"].iloc[-1]
        exit_px    = last_close * (1 - TC)
        ret        = (exit_px / entry_price) - 1.0
        equity    *= (1 + ret)
        trades.append({
            "type":        "final_close",
            "entry_date":  entry_date,
            "exit_date":   last_date,
            "entry_price": entry_price / (1 + TC),
            "exit_price":  last_close,
            "return_pct":  ret * 100,
            "hold_days":   (last_date - entry_date).days,
            "trigger":     "end_of_backtest",
        })
        equity_rows[-1]["equity"] = equity

    eq_df = pd.DataFrame(equity_rows).set_index("date")
    closes = prices.set_index("date")["close_price"].reindex(eq_df.index)
    eq_df["bnh"] = closes / closes.iloc[0]

    return pd.DataFrame(trades), eq_df, pd.DataFrame(cash_periods)


def metrics(s, label):
    eq = s.values
    rets = np.diff(eq) / eq[:-1]
    n = len(eq); yrs = n / 252
    cagr = (eq[-1]/eq[0])**(1/yrs) - 1
    vol  = rets.std() * np.sqrt(252)
    shrp = (rets.mean()*252) / vol if vol > 0 else 0
    dd   = ((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min()
    return {"label": label, "total_ret": (eq[-1]/eq[0]-1)*100,
            "cagr": cagr*100, "vol": vol*100, "sharpe": shrp,
            "max_dd": dd*100, "calmar": cagr/abs(dd) if dd != 0 else 0}


def print_report(trades, equity, cash_periods):
    sm = metrics(equity["equity"], "Bear-Exit Strategy")
    bm = metrics(equity["bnh"],    "Buy-and-Hold SPY")
    tim = equity["in_mkt"].mean() * 100
    sep = "=" * 68
    dr  = f"{equity.index[0].date()} to {equity.index[-1].date()}"

    exits  = trades[trades["type"] == "exit"]
    n_bear = len(exits)

    print(sep)
    print("  SPY BEAR-EXIT GAP-FILL STRATEGY")
    print(f"  Stay long - exit on bear block - re-enter on gap fill")
    print(f"  Period: {dr}  ({len(equity)} trading days)")
    print(sep)
    print(f"\n  Bear exits triggered  : {n_bear}")
    print(f"  Time in market        : {tim:.1f}%")
    if len(cash_periods):
        cp = pd.DataFrame(cash_periods) if not isinstance(cash_periods, pd.DataFrame) else cash_periods
        if len(cp):
            print(f"  Avg cash period       : {cp['cash_days'].mean():.1f} days")
            print(f"  Median cash period    : {cp['cash_days'].median():.0f} days")
    print()

    print(f"  {'Metric':<22}  {'Bear-Exit':>10}  {'BnH SPY':>10}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*10}")
    for lab, key, unit in [
        ("Total Return",     "total_ret", "%"),
        ("CAGR",             "cagr",      "%"),
        ("Volatility (ann)", "vol",       "%"),
        ("Sharpe Ratio",     "sharpe",    "x"),
        ("Max Drawdown",     "max_dd",    "%"),
        ("Calmar Ratio",     "calmar",    "x"),
    ]:
        sv, bv = sm[key], bm[key]
        if unit == "%":
            print(f"  {lab:<22}  {sv:>+9.2f}%  {bv:>+9.2f}%")
        else:
            print(f"  {lab:<22}  {sv:>10.2f}  {bv:>10.2f}")

    print(f"\n  Cash Period Log (each bear exit)")
    print(f"  {'-'*68}")
    print(f"  {'Exit':>10}  {'Reentry':>10}  {'Days':>4}  {'Fill':>8}  Reason")
    print(f"  {'-'*10}  {'-'*10}  {'-'*4}  {'-'*8}  {'-'*20}")
    if isinstance(cash_periods, pd.DataFrame) and len(cash_periods):
        for _, cp in cash_periods.iterrows():
            ed = cp["exit_date"].date() if hasattr(cp["exit_date"], "date") else cp["exit_date"]
            rd = cp["reentry_date"].date() if hasattr(cp["reentry_date"], "date") else cp["reentry_date"]
            print(f"  {str(ed):>10}  {str(rd):>10}  {int(cp['cash_days']):>4}"
                  f"  {cp['fill_h']:>8}  {cp['reason']}")

    print(f"\n  Annual Returns")
    print(f"  {'-'*44}")
    eq_yr = equity.copy(); eq_yr["year"] = eq_yr.index.year
    for yr, grp in eq_yr.groupby("year"):
        s = (grp["equity"].iloc[-1] / grp["equity"].iloc[0] - 1) * 100
        b = (grp["bnh"].iloc[-1]    / grp["bnh"].iloc[0]    - 1) * 100
        t = grp["in_mkt"].mean() * 100
        print(f"  {yr}:  Strategy {s:>+7.2f}%   SPY {b:>+7.2f}%   in-mkt {t:.0f}%")
    print(f"\n{sep}")


def main():
    for f in [OUTCOMES_CSV, PRICES_CSV]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found"); sys.exit(1)

    outcomes, prices = load()
    first_sig = outcomes["trade_date"].min()
    prices = prices[prices["date"] >= first_sig].reset_index(drop=True)

    trades, equity, cash_periods = run_backtest(prices, outcomes)
    print_report(trades, equity, cash_periods)

    trades.to_csv(TRADES_OUT, index=False)
    equity.reset_index().to_csv(EQUITY_OUT, index=False)
    print(f"  Saved: {TRADES_OUT}")
    print(f"  Saved: {EQUITY_OUT}")


if __name__ == "__main__":
    main()
