"""
blockGapFillStrategy.py
-----------------------
SPY block gap-fill strategy backtest.

Each above-market block is treated as an INDEPENDENT trade:
  - Entry: T+1 open price
  - Exit: close on the first day price reached the block price
  - Stop: 1mo close if never reached (1mo_stop)
  - Trades run in parallel - equal weight across all concurrent positions

Equal-weight portfolio: each day's equity = average MTM across all live trades.
Normalized to $1 starting equity at first signal date.

Below-market blocks are excluded (no shorts).
"""

import os, sys
import numpy as np
import pandas as pd

EVENTS_CSV   = os.path.expanduser("~/discordBot/outputs/research/block_events.csv")
OUTCOMES_CSV = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")
PRICES_CSV   = os.path.expanduser("~/discordBot/outputs/markets/cache/spy_vwap_daily.csv")
TRADES_OUT   = os.path.expanduser("~/discordBot/outputs/research/block_gap_fill_trades.csv")
EQUITY_OUT   = os.path.expanduser("~/discordBot/outputs/research/block_gap_fill_equity.csv")

TC = 0.0001
HORIZON_DAYS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1mo": 21}


def first_fill(row):
    for h, d in HORIZON_DAYS.items():
        if str(row.get("reached_" + h, "")).lower() == "true":
            return d, h, float(row.get("close_" + h, np.nan))
    return 21, "1mo_stop", float(row.get("close_1mo", np.nan))


def nth_trading_day_after(date_index, ref_date, n):
    future = date_index[date_index > ref_date]
    return future[n - 1] if len(future) >= n else (future[-1] if len(future) else None)


def load():
    outcomes = pd.read_csv(OUTCOMES_CSV, parse_dates=["trade_date"])
    prices   = pd.read_csv(PRICES_CSV,   parse_dates=["date"])
    prices   = prices[["date", "open_price", "close_price"]].dropna().sort_values("date").reset_index(drop=True)

    above = outcomes[outcomes["direction"] == "above_market"].copy()
    above[["fill_days", "fill_horizon", "fill_close"]] = above.apply(
        lambda r: pd.Series(first_fill(r)), axis=1)
    above = above.sort_values("trade_date").reset_index(drop=True)
    return above, prices


def build_trades(above: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    date_idx  = pd.DatetimeIndex(prices["date"])
    open_map  = dict(zip(prices["date"], prices["open_price"]))
    close_map = dict(zip(prices["date"], prices["close_price"]))

    rows = []
    for _, sig in above.iterrows():
        signal_date = sig["trade_date"]

        # Entry: first trading day after signal_date
        entry_date = nth_trading_day_after(date_idx, signal_date, 1)
        if entry_date is None:
            continue
        entry_open = open_map.get(entry_date)
        if entry_open is None:
            continue

        # Exit: fill_days trading days after signal_date
        fill_days   = int(sig["fill_days"])
        exit_date   = nth_trading_day_after(date_idx, signal_date, fill_days)
        if exit_date is None:
            continue

        fill_close = sig["fill_close"]
        if np.isnan(fill_close):
            continue

        # Use the actual close on exit_date (not the stored fill_close which could be stale)
        exit_close = close_map.get(exit_date, fill_close)

        entry_px_net = entry_open * (1 + TC)
        exit_px_net  = exit_close * (1 - TC)
        ret          = (exit_px_net / entry_px_net) - 1.0

        rows.append({
            "event_idx":    int(sig["event_idx"]),
            "signal_date":  signal_date,
            "entry_date":   entry_date,
            "exit_date":    exit_date,
            "block_price":  float(sig["block_price"]),
            "market_price": float(sig["market_price"]),
            "entry_open":   entry_open,
            "exit_close":   exit_close,
            "entry_px_net": entry_px_net,
            "exit_px_net":  exit_px_net,
            "return_pct":   ret * 100,
            "hold_days":    (exit_date - entry_date).days,
            "fill_horizon": sig["fill_horizon"],
            "is_stop":      bool(sig["fill_horizon"] == "1mo_stop"),
            "deviation":    float(sig["deviation"]),
            "dollar_value": float(sig["dollar_value"]),
            "exchange":     str(sig["exchange"]),
            "gap_pct":      (float(sig["block_price"]) - float(sig["market_price"])) / float(sig["market_price"]) * 100,
        })

    return pd.DataFrame(rows)


def build_equity(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Equal-weight portfolio equity curve.
    Each day: avg(MTM of all live trades).
    Live trade MTM = close_px / entry_open (gross, before TC - TC applied at open/close).
    """
    close_map = dict(zip(prices["date"], prices["close_price"]))
    date_list = list(prices["date"])

    equity_rows = []
    for day in date_list:
        close_px = close_map.get(day)
        if close_px is None:
            continue

        # Live trades on this day: entry_date <= day < exit_date
        live = trades[(trades["entry_date"] <= day) & (trades["exit_date"] > day)]

        if live.empty:
            # Cash day - find last equity value
            prev = [r["equity"] for r in equity_rows if r["equity"] is not None]
            equity_rows.append({"date": day, "equity": prev[-1] if prev else 1.0,
                                 "n_positions": 0, "in_mkt": 0})
        else:
            # MTM each live position relative to its entry
            mtm_rets = []
            for _, t in live.iterrows():
                mtm_ret = (close_px * (1 - TC)) / t["entry_px_net"] - 1.0
                mtm_rets.append(mtm_ret)

            avg_ret = np.mean(mtm_rets)

            # Compound into equity: today's equity = prev equity * (1 + avg_daily_ret)
            # Actually: equity = (1 + cumulative return from all completed trades) * current open position factor
            # Simpler & correct for parallel trades: track portfolio value directly
            prev_eq = equity_rows[-1]["equity"] if equity_rows else 1.0
            # Daily portfolio return = avg of live position daily returns
            prev_close_map = {r["date"]: r.get("_close") for r in equity_rows}
            # Use prev close to compute daily return
            equity_rows.append({"date": day, "equity": None,  # fill below
                                 "n_positions": len(live), "in_mkt": 1,
                                 "_live_ids": list(live["event_idx"]),
                                 "_close": close_px})

        # Will recompute properly below
    # Recompute equity properly as daily P&L
    return _recompute_equity(trades, prices)


def _recompute_equity(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Proper daily P&L: start with $1. Each day, portfolio = avg of all live positions MTM.
    Positions enter at T+1 open, exit at exit_date close. Cash earns 0% between trades.

    Portfolio daily return = weighted avg of live position returns for that day.
    """
    close_map = dict(zip(prices["date"], prices["close_price"]))
    open_map  = dict(zip(prices["date"], prices["open_price"]))

    date_list = sorted(prices["date"].tolist())

    # For each trade, build a daily return series
    # daily_ret[trade] = close_today / close_yesterday (or open on entry day, close on exit day)
    trade_daily = {}
    for _, t in trades.iterrows():
        eid = t["event_idx"]
        t_dates = [d for d in date_list if t["entry_date"] <= d <= t["exit_date"]]
        rets = {}
        for i, d in enumerate(t_dates):
            if i == 0:
                # Entry day: open to close
                op = t["entry_px_net"]   # open + TC
                cl = close_map.get(d, op)
                rets[d] = (cl / op) - 1.0
            elif d == t["exit_date"]:
                # Exit day: prev close to exit close (net of TC)
                prev_d = t_dates[i-1]
                prev_c = close_map.get(prev_d, close_map.get(d))
                cl = t["exit_px_net"]
                rets[d] = (cl / prev_c) - 1.0
            else:
                # Mid: prev close to today close
                prev_d = t_dates[i-1]
                prev_c = close_map.get(prev_d, close_map.get(d))
                cl = close_map.get(d, prev_c)
                rets[d] = (cl / prev_c) - 1.0
        trade_daily[eid] = rets

    equity = 1.0
    rows = []
    for day in date_list:
        # Find live positions on this day
        live = trades[(trades["entry_date"] <= day) & (trades["exit_date"] >= day)]

        if live.empty:
            rows.append({"date": day, "equity": equity, "n_positions": 0, "in_mkt": 0})
            continue

        # Portfolio return = equal-weight avg of live position daily returns
        day_rets = []
        for _, t in live.iterrows():
            eid = t["event_idx"]
            dr = trade_daily.get(eid, {}).get(day, 0.0)
            day_rets.append(dr)

        port_ret = np.mean(day_rets)
        equity  *= (1 + port_ret)
        rows.append({"date": day, "equity": equity, "n_positions": len(live), "in_mkt": 1})

    df = pd.DataFrame(rows).set_index("date")

    # Add BnH
    closes = prices.set_index("date")["close_price"].reindex(df.index)
    df["bnh"] = closes / closes.iloc[0]

    return df


def metrics(s: pd.Series, label: str) -> dict:
    eq = s.values
    rets = np.diff(eq) / eq[:-1]
    n = len(eq); yrs = n / 252
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    vol  = rets.std() * np.sqrt(252)
    shrp = (rets.mean() * 252) / vol if vol > 0 else 0
    dd   = ((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min()
    return {"label": label, "total_ret": (eq[-1]/eq[0]-1)*100,
            "cagr": cagr*100, "vol": vol*100, "sharpe": shrp,
            "max_dd": dd*100, "calmar": cagr/abs(dd) if dd != 0 else 0}


def trade_stats(trades: pd.DataFrame) -> dict:
    r = trades["return_pct"]
    wins = r[r > 0]; loss = r[r <= 0]
    stops = trades[trades["is_stop"]]; fills = trades[~trades["is_stop"]]
    return {
        "n": len(trades), "n_stops": len(stops),
        "win_rate":     (r > 0).mean() * 100,
        "avg_win":      wins.mean() if len(wins) else 0,
        "avg_loss":     loss.mean() if len(loss) else 0,
        "pf":           wins.sum() / abs(loss.sum()) if len(loss) and loss.sum() != 0 else float("inf"),
        "avg_hold":     trades["hold_days"].mean(),
        "med_hold":     trades["hold_days"].median(),
        "best":         r.max(), "worst": r.min(),
        "fill_avg":     fills["return_pct"].mean() if len(fills) else 0,
        "stop_avg":     stops["return_pct"].mean() if len(stops) else 0,
    }


def print_report(trades: pd.DataFrame, equity: pd.DataFrame):
    sm = metrics(equity["equity"], "Gap-Fill")
    bm = metrics(equity["bnh"],    "BnH SPY")
    ts = trade_stats(trades)
    tim = equity["in_mkt"].mean() * 100
    sep = "=" * 68
    dr  = f"{equity.index[0].date()} to {equity.index[-1].date()}"

    print(sep)
    print("  SPY BLOCK GAP-FILL STRATEGY (parallel equal-weight)")
    print(f"  Period: {dr}  ({len(equity)} trading days)")
    print(sep)
    print(f"\n  Trades: {ts['n']}  |  Stops: {ts['n_stops']}  |  Avg positions/day: {equity['n_positions'].mean():.1f}")
    print(f"  Days with any position: {equity['in_mkt'].sum()} ({tim:.1f}%)\n")

    print(f"  {'Metric':<22}  {'Gap-Fill':>10}  {'BnH SPY':>10}")
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

    print(f"\n  Trade Stats  (each block = independent trade)")
    print(f"  {'-'*44}")
    for lab, val, fmt in [
        ("Win rate",           ts["win_rate"],  "f1p"),
        ("Avg win",            ts["avg_win"],   "f2p+"),
        ("Avg loss",           ts["avg_loss"],  "f2p"),
        ("Profit factor",      ts["pf"],        "f2x"),
        ("Avg hold (cal days)",ts["avg_hold"],  "f1d"),
        ("Median hold",        ts["med_hold"],  "f0d"),
        ("Best trade",         ts["best"],      "f2p+"),
        ("Worst trade",        ts["worst"],     "f2p"),
        ("Fill trades avg",    ts["fill_avg"],  "f2p+"),
        ("Stop trades avg",    ts["stop_avg"],  "f2p+"),
    ]:
        if fmt == "f1p":
            print(f"  {lab:<22}  {val:.1f}%")
        elif fmt == "f2p+":
            print(f"  {lab:<22}  {val:+.2f}%")
        elif fmt == "f2p":
            print(f"  {lab:<22}  {val:.2f}%")
        elif fmt == "f2x":
            print(f"  {lab:<22}  {val:.2f}x")
        elif fmt == "f1d":
            print(f"  {lab:<22}  {val:.1f} days")
        elif fmt == "f0d":
            print(f"  {lab:<22}  {val:.0f} days")

    print(f"\n  Fill Horizon Breakdown")
    print(f"  {'-'*50}")
    print(f"  {'Horizon':<12}  {'N':>4}  {'Avg Ret':>8}  {'Win%':>6}  {'Avg Hold':>9}")
    print(f"  {'-'*12}  {'-'*4}  {'-'*8}  {'-'*6}  {'-'*9}")
    for h in ["1d","3d","1w","2w","1mo","1mo_stop"]:
        grp = trades[trades["fill_horizon"] == h]
        if len(grp) == 0:
            continue
        ar  = grp["return_pct"].mean()
        wr  = (grp["return_pct"] > 0).mean() * 100
        ah  = grp["hold_days"].mean()
        tag = " *stop" if h == "1mo_stop" else ""
        print(f"  {h+tag:<12}  {len(grp):>4}  {ar:>+7.2f}%  {wr:>5.0f}%  {ah:>8.1f}d")

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
    for f in [EVENTS_CSV, OUTCOMES_CSV, PRICES_CSV]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found"); sys.exit(1)

    above, prices = load()
    first_sig = above["trade_date"].min()
    prices = prices[prices["date"] >= first_sig].reset_index(drop=True)

    trades = build_trades(above, prices)
    equity = _recompute_equity(trades, prices)

    print_report(trades, equity)

    trades.to_csv(TRADES_OUT, index=False)
    equity.reset_index().to_csv(EQUITY_OUT, index=False)
    print(f"  Saved trades -> {TRADES_OUT}")
    print(f"  Saved equity -> {EQUITY_OUT}")


if __name__ == "__main__":
    main()
