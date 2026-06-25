"""
hybridBacktests.py
------------------
Three hybrid strategies using SPY block order signals.
All use the same infrastructure as blockBearExitStrategy.py.

Strategy A: Hedged Long (75/25 SPY/SH)
  - Always 100% long SPY
  - Bear signal -> shift 25% of portfolio to SH at T+1 open (keep 75% SPY)
  - Re-enter 25% back to SPY (sell SH) when bear gap fills or 1mo stop
  - No full exits - always have SPY exposure

Strategy B: High-Dev Bear Filter
  - Same as bear-exit strategy but ONLY act on bear blocks with deviation >= 0.8%
  - Low-dev bear blocks (<0.8%) are ignored (stay long)

Strategy C: Regime-Gated Bear Exit
  - Same as bear-exit but only exit when current regime is 'bear' or 'chop'
  - Bull regime bear blocks are ignored
  - (No-op on Jun2025-Jun2026 window - all bear signals already in chop/bear)
  - Code it correctly for when backfill data arrives

All strategies also compared against:
  - Buy-and-Hold SPY
  - Original bear-exit (from blockBearExitStrategy.py)

Outputs:
  outputs/research/hybrid_equity.csv    (date + all strategy equity curves)
  outputs/research/hybrid_trades_A.csv
  outputs/research/hybrid_trades_B.csv
  outputs/research/hybrid_trades_C.csv
"""

import os, sys
import numpy as np
import pandas as pd

OUTCOMES_CSV = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")
PRICES_CSV   = os.path.expanduser("~/discordBot/outputs/markets/cache/spy_vwap_daily.csv")
SH_CSV       = os.path.expanduser("~/discordBot/outputs/markets/cache/sh_daily.csv")
FEATS_CSV    = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
EQUITY_OUT   = os.path.expanduser("~/discordBot/outputs/research/hybrid_equity.csv")

TC = 0.0001
HORIZONS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1mo": 21}
HIGH_DEV_THRESHOLD = 0.008


# ── shared helpers ─────────────────────────────────────────────────────────────

def nth_after(date_idx, ref, n):
    fut = date_idx[date_idx > ref]
    return fut[n - 1] if len(fut) >= n else (fut[-1] if len(fut) else None)


def load_base():
    outcomes = pd.read_csv(OUTCOMES_CSV, parse_dates=["trade_date"])
    prices   = pd.read_csv(PRICES_CSV,   parse_dates=["date"])
    sh       = pd.read_csv(SH_CSV,       parse_dates=["date"])
    feats    = pd.read_csv(FEATS_CSV,    parse_dates=["date"])

    prices = prices[["date","open_price","close_price"]].dropna().sort_values("date").reset_index(drop=True)
    sh     = sh[["date","open","close"]].dropna().sort_values("date").reset_index(drop=True)
    feats  = feats[["date","regime"]].dropna(subset=["regime"]).sort_values("date")

    for h in HORIZONS:
        outcomes["reached_" + h] = outcomes["reached_" + h].astype(str).str.lower() == "true"

    return outcomes, prices, sh, feats


def build_bear_map(outcomes, date_idx, dev_threshold=None, allowed_regimes=None, feats=None):
    """
    Returns dict: exit_date -> {signal_date, reentry_date, fill_h, block_price,
                                 market_price, deviation, dollar_value}
    dev_threshold: if set, only include bear blocks with deviation >= threshold
    allowed_regimes: if set, only include blocks where regime is in this set
    """
    below = outcomes[outcomes["direction"] == "below_market"].copy()

    if dev_threshold is not None:
        below = below[below["deviation"] >= dev_threshold]

    if allowed_regimes is not None and feats is not None:
        regime_map = dict(zip(feats["date"], feats["regime"]))
        below["_regime"] = below["trade_date"].map(regime_map)
        below = below[below["_regime"].isin(allowed_regimes)]

    below = below.sort_values("dollar_value", ascending=False)
    bear  = below.drop_duplicates("trade_date", keep="first").sort_values("trade_date")

    result = {}
    for _, row in bear.iterrows():
        sig_date  = row["trade_date"]
        exit_date = nth_after(date_idx, sig_date, 1)
        if exit_date is None:
            continue

        reentry_date = None
        fill_h = "never"
        for h, d in HORIZONS.items():
            if row["reached_" + h]:
                fill_day     = nth_after(date_idx, sig_date, d)
                reentry_date = nth_after(date_idx, fill_day, 1) if fill_day else None
                fill_h = h
                break
        if reentry_date is None:
            fill_day     = nth_after(date_idx, sig_date, 21)
            reentry_date = nth_after(date_idx, fill_day, 1) if fill_day else None
            fill_h = "1mo_stop"

        result[exit_date] = {
            "signal_date":  sig_date,
            "reentry_date": reentry_date,
            "fill_h":       fill_h,
            "block_price":  float(row["block_price"]),
            "market_price": float(row["market_price"]),
            "deviation":    float(row["deviation"]),
            "dollar_value": float(row["dollar_value"]),
        }
    return result


def build_bull_entry_map(outcomes, date_idx):
    """Returns dict: entry_date -> signal_date for above-market blocks."""
    above = outcomes[outcomes["direction"] == "above_market"].copy()
    above = above.sort_values("dollar_value", ascending=False).drop_duplicates("trade_date", keep="first")
    result = {}
    for _, row in above.iterrows():
        entry = nth_after(date_idx, row["trade_date"], 1)
        if entry and entry not in result:
            result[entry] = row["trade_date"]
    return result


def metrics(s, label):
    eq = s.dropna().values
    if len(eq) < 2:
        return {"label": label, "total_ret": 0, "cagr": 0, "vol": 0,
                "sharpe": 0, "max_dd": 0, "calmar": 0}
    rets = np.diff(eq) / eq[:-1]
    n = len(eq); yrs = n / 252
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    vol  = rets.std() * np.sqrt(252)
    shrp = (rets.mean() * 252) / vol if vol > 0 else 0
    dd   = ((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min()
    return {"label": label,
            "total_ret": (eq[-1] / eq[0] - 1) * 100,
            "cagr":      cagr * 100,
            "vol":       vol * 100,
            "sharpe":    shrp,
            "max_dd":    dd * 100,
            "calmar":    cagr / abs(dd) if dd != 0 else 0}


# ── Strategy A: 75/25 Hedged Long ─────────────────────────────────────────────

def run_hedged(outcomes, prices, sh, feats):
    """
    Always hold SPY. On bear signal: shift 25% of current portfolio value to SH.
    Re-balance back to 100% SPY when gap fills.
    Tracks actual share counts for each leg.
    """
    date_idx  = pd.DatetimeIndex(prices["date"])
    spy_open  = dict(zip(prices["date"], prices["open_price"]))
    spy_close = dict(zip(prices["date"], prices["close_price"]))
    sh_open   = dict(zip(sh["date"],     sh["open"]))
    sh_close  = dict(zip(sh["date"],     sh["close"]))

    bear_map = build_bear_map(outcomes, date_idx)
    bull_map = build_bull_entry_map(outcomes, date_idx)

    # Start fully long SPY
    first_open   = prices.iloc[0]["open_price"]
    spy_shares   = (1.0 / (first_open * (1 + TC)))  # shares of SPY per $1 initial
    sh_shares    = 0.0
    hedged       = False
    pending_act  = None   # (date, 'hedge' | 'unhedge', sig)
    equity_rows  = []
    trades       = []

    for _, row in prices.iterrows():
        today    = row["date"]
        s_open   = spy_open.get(today)
        s_close  = spy_close.get(today)
        h_open   = sh_open.get(today)
        h_close  = sh_close.get(today)
        if s_open is None or s_close is None:
            continue

        # ── Execute pending action at today's open ─────────────────────────────
        if pending_act and pending_act[0] == today:
            action = pending_act[1]
            sig    = pending_act[2]

            if action == "hedge" and not hedged and h_open:
                # Sell 25% of SPY position, buy SH with proceeds
                portfolio_val    = spy_shares * s_open       # total value before rebalance
                spy_sell_val     = portfolio_val * 0.25      # sell 25% of SPY
                spy_shares_sell  = spy_sell_val / (s_open * (1 + TC))
                spy_shares      -= spy_shares_sell
                sh_buy_val       = spy_sell_val * (1 - TC)   # proceeds after selling SPY
                sh_shares        = sh_buy_val / (h_open * (1 + TC))
                hedged           = True
                trades.append({
                    "type": "hedge_on", "date": today,
                    "signal_date": sig["signal_date"],
                    "block_price": sig["block_price"],
                    "sh_entry": h_open,
                    "fill_h": sig["fill_h"],
                    "reentry_date": sig.get("reentry_date"),
                })

            elif action == "unhedge" and hedged and h_open:
                # Sell all SH, put into SPY
                sh_val    = sh_shares * (h_open * (1 - TC))
                spy_buy   = sh_val / (s_open * (1 + TC))
                spy_shares += spy_buy
                sh_ret_pct = (h_open / sig.get("sh_entry", h_open) - 1) * 100 if sig.get("sh_entry") else 0
                sh_shares  = 0.0
                hedged     = False
                trades.append({
                    "type": "hedge_off", "date": today,
                    "sh_ret": sh_ret_pct,
                })

            pending_act = None

        # ── Queue hedge on bear signal (exit_date == today) ────────────────────
        if today in bear_map and not hedged and pending_act is None:
            sig = bear_map[today]
            # Execute hedge immediately at today's open (bear_map keyed by exit_date=T+1)
            if h_open:
                portfolio_val   = spy_shares * s_open
                spy_sell_val    = portfolio_val * 0.25
                spy_shares_sell = spy_sell_val / (s_open * (1 + TC))
                spy_shares     -= spy_shares_sell
                sh_buy_val      = spy_sell_val * (1 - TC)
                sh_shares       = sh_buy_val / (h_open * (1 + TC))
                hedged          = True
                trades.append({
                    "type": "hedge_on", "date": today,
                    "signal_date": sig["signal_date"],
                    "block_price": sig["block_price"],
                    "sh_entry": h_open,
                    "fill_h": sig["fill_h"],
                    "reentry_date": sig.get("reentry_date"),
                })
                if sig.get("reentry_date"):
                    pending_act = (sig["reentry_date"], "unhedge",
                                   {"sh_entry": h_open, "reentry_date": sig["reentry_date"]})

        # ── Early unhedge if bull signal fires before scheduled rebalance ──────
        if hedged and pending_act and pending_act[1] == "unhedge":
            for bull_entry in bull_map:
                if bull_entry == today and bull_entry < pending_act[0]:
                    pending_act = (today, "unhedge", pending_act[2])
                    break

        # Re-check pending unhedge for today
        if pending_act and pending_act[0] == today and pending_act[1] == "unhedge" and hedged and h_open:
            sig = pending_act[2]
            sh_val     = sh_shares * (h_open * (1 - TC))
            spy_buy    = sh_val / (s_open * (1 + TC))
            spy_shares += spy_buy
            sh_ret_pct = (h_open / sig.get("sh_entry", h_open) - 1) * 100 if sig.get("sh_entry") else 0
            sh_shares  = 0.0
            hedged     = False
            trades.append({"type": "hedge_off", "date": today, "sh_ret": sh_ret_pct})
            pending_act = None

        # ── MTM at close ───────────────────────────────────────────────────────
        spy_val = spy_shares * s_close
        sh_val  = sh_shares  * (h_close if h_close else 0)
        total   = spy_val + sh_val

        equity_rows.append({
            "date":     today,
            "equity_A": total,
            "hedged":   int(hedged),
            "spy_pct":  spy_val / total if total > 0 else 0,
        })

    eq_df = pd.DataFrame(equity_rows).set_index("date")
    eq_df["equity_A"] = eq_df["equity_A"] / eq_df["equity_A"].iloc[0]
    return pd.DataFrame(trades), eq_df


# ── Strategy B: High-Dev Bear Filter ──────────────────────────────────────────

def run_highdev_filter(outcomes, prices, feats):
    """Bear-exit strategy but only on high-dev (>=0.8%) bear blocks."""
    date_idx  = pd.DatetimeIndex(prices["date"])
    open_map  = dict(zip(prices["date"], prices["open_price"]))
    close_map = dict(zip(prices["date"], prices["close_price"]))

    bear_map = build_bear_map(outcomes, date_idx, dev_threshold=HIGH_DEV_THRESHOLD)
    bull_map = build_bull_entry_map(outcomes, date_idx)

    position      = "long"
    entry_price   = None
    entry_date    = None
    pending_entry = None
    equity        = 1.0
    cash_sig      = None
    equity_rows   = []
    trades        = []

    first = prices.iloc[0]
    entry_price = first["open_price"] * (1 + TC)
    entry_date  = first["date"]

    for _, row in prices.iterrows():
        today   = row["date"]
        open_px = row["open_price"]
        close_px= row["close_price"]

        # Execute pending re-entry
        if pending_entry and pending_entry == today and position == "cash":
            entry_price = open_px * (1 + TC)
            entry_date  = today
            position    = "long"
            pending_entry = None
            cash_sig    = None

        # Bear exit
        if position == "long" and today in bear_map:
            sig = bear_map[today]
            exit_px = open_px * (1 - TC)
            ret     = (exit_px / entry_price) - 1.0
            equity *= (1 + ret)
            trades.append({"exit_date": today, "return_pct": ret * 100,
                           "fill_h": sig["fill_h"], "deviation": sig["deviation"],
                           "signal_date": sig["signal_date"]})
            position    = "cash"
            entry_price = None
            entry_date  = None
            cash_sig    = sig
            pending_entry = sig["reentry_date"]

        # Early bull override
        if position == "cash" and pending_entry:
            if today in bull_map and today < pending_entry:
                pending_entry = today

        if pending_entry and pending_entry == today and position == "cash":
            entry_price = open_px * (1 + TC)
            entry_date  = today
            position    = "long"
            pending_entry = None
            cash_sig    = None

        # MTM
        if position == "long" and entry_price:
            raw = entry_price / (1 + TC)
            eq_today = equity * (close_px / raw)
        else:
            eq_today = equity

        equity_rows.append({"date": today, "equity_B": eq_today,
                             "in_mkt": int(position == "long")})

    if position == "long" and entry_price:
        last = prices["close_price"].iloc[-1]
        ret  = (last * (1-TC) / entry_price) - 1.0
        equity *= (1 + ret)
        equity_rows[-1]["equity_B"] = equity

    eq_df = pd.DataFrame(equity_rows).set_index("date")
    eq_df["equity_B"] = eq_df["equity_B"] / eq_df["equity_B"].iloc[0]
    return pd.DataFrame(trades), eq_df


# ── Strategy C: Regime-Gated Bear Exit ────────────────────────────────────────

def run_regime_gated(outcomes, prices, feats):
    """Bear-exit only when regime is bear or chop. Bull regime = ignore bear signal."""
    date_idx  = pd.DatetimeIndex(prices["date"])
    open_map  = dict(zip(prices["date"], prices["open_price"]))
    close_map = dict(zip(prices["date"], prices["close_price"]))

    # Only fire in bear or chop
    bear_map = build_bear_map(outcomes, date_idx,
                               allowed_regimes={"bear", "chop"}, feats=feats)
    bull_map = build_bull_entry_map(outcomes, date_idx)

    position      = "long"
    entry_price   = None
    entry_date    = None
    pending_entry = None
    equity        = 1.0
    equity_rows   = []
    trades        = []

    first = prices.iloc[0]
    entry_price = first["open_price"] * (1 + TC)
    entry_date  = first["date"]

    for _, row in prices.iterrows():
        today   = row["date"]
        open_px = row["open_price"]
        close_px= row["close_price"]

        if pending_entry and pending_entry == today and position == "cash":
            entry_price   = open_px * (1 + TC)
            entry_date    = today
            position      = "long"
            pending_entry = None

        if position == "long" and today in bear_map:
            sig = bear_map[today]
            exit_px = open_px * (1 - TC)
            ret     = (exit_px / entry_price) - 1.0
            equity *= (1 + ret)
            trades.append({"exit_date": today, "return_pct": ret * 100,
                           "fill_h": sig["fill_h"], "signal_date": sig["signal_date"]})
            position      = "cash"
            entry_price   = None
            pending_entry = sig["reentry_date"]

        if position == "cash" and pending_entry:
            if today in bull_map and today < pending_entry:
                pending_entry = today

        if pending_entry and pending_entry == today and position == "cash":
            entry_price   = open_px * (1 + TC)
            entry_date    = today
            position      = "long"
            pending_entry = None

        if position == "long" and entry_price:
            raw = entry_price / (1 + TC)
            eq_today = equity * (close_px / raw)
        else:
            eq_today = equity

        equity_rows.append({"date": today, "equity_C": eq_today,
                             "in_mkt": int(position == "long")})

    if position == "long" and entry_price:
        last = prices["close_price"].iloc[-1]
        ret  = (last * (1-TC) / entry_price) - 1.0
        equity *= (1 + ret)
        equity_rows[-1]["equity_C"] = equity

    eq_df = pd.DataFrame(equity_rows).set_index("date")
    eq_df["equity_C"] = eq_df["equity_C"] / eq_df["equity_C"].iloc[0]
    return pd.DataFrame(trades), eq_df


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    for f in [OUTCOMES_CSV, PRICES_CSV, SH_CSV, FEATS_CSV]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found"); sys.exit(1)

    outcomes, prices, sh, feats = load_base()
    first_sig = outcomes["trade_date"].min()
    prices = prices[prices["date"] >= first_sig].reset_index(drop=True)

    print("Running Strategy A: Hedged Long (75% SPY / 25% SH on bear)...")
    trades_A, eq_A = run_hedged(outcomes, prices, sh, feats)
    trades_A.to_csv(os.path.expanduser("~/discordBot/outputs/research/hybrid_trades_A.csv"), index=False)

    print("Running Strategy B: High-Dev Bear Filter (>=0.8% only)...")
    trades_B, eq_B = run_highdev_filter(outcomes, prices, feats)
    trades_B.to_csv(os.path.expanduser("~/discordBot/outputs/research/hybrid_trades_B.csv"), index=False)

    print("Running Strategy C: Regime-Gated Bear Exit...")
    trades_C, eq_C = run_regime_gated(outcomes, prices, feats)
    trades_C.to_csv(os.path.expanduser("~/discordBot/outputs/research/hybrid_trades_C.csv"), index=False)

    # Load bear-exit baseline equity
    bear_eq = pd.read_csv(
        os.path.expanduser("~/discordBot/outputs/research/bear_exit_equity.csv"),
        parse_dates=["date"]
    ).set_index("date")

    # Combine all equity curves
    closes = prices.set_index("date")["close_price"]
    bnh    = (closes / closes.iloc[0]).rename("bnh")

    eq_combined = (eq_A[["equity_A"]]
                   .join(eq_B[["equity_B"]], how="outer")
                   .join(eq_C[["equity_C"]], how="outer")
                   .join(bear_eq[["equity"]].rename(columns={"equity": "bear_exit"}), how="outer")
                   .join(bnh, how="outer")
                   .reset_index())

    eq_combined.to_csv(EQUITY_OUT, index=False)
    print(f"Saved equity curves -> {EQUITY_OUT}")
    print()

    # Print summary metrics
    sep = "=" * 72
    print(sep)
    print("  HYBRID STRATEGY COMPARISON")
    print(sep)
    strats = [
        ("equity_A",  "Hedged Long (75% SPY / 25% SH)"),
        ("equity_B",  "High-Dev Filter (bear >=0.8% only)"),
        ("equity_C",  "Regime-Gated Exit (bear/chop only)"),
        ("bear_exit", "Bear-Exit Baseline"),
        ("bnh",       "Buy-and-Hold SPY"),
    ]
    print(f"  {'Strategy':<36}  {'TotRet':>7}  {'CAGR':>7}  {'Sharpe':>7}  {'MaxDD':>7}  {'Calmar':>7}")
    print(f"  {'-'*36}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}")
    for col, label in strats:
        s = eq_combined.set_index("date")[col].dropna()
        m = metrics(s, label)
        print(f"  {label:<36}  {m['total_ret']:>+6.1f}%  {m['cagr']:>+6.1f}%"
              f"  {m['sharpe']:>7.2f}  {m['max_dd']:>+6.1f}%  {m['calmar']:>7.2f}")
    print(sep)
    print()

    # B-specific: how many bear signals filtered out vs original
    all_bear = outcomes[outcomes["direction"] == "below_market"]
    bear_daily_all = all_bear.drop_duplicates("trade_date")
    hd_bear = bear_daily_all[bear_daily_all["deviation"] >= HIGH_DEV_THRESHOLD]
    ld_bear = bear_daily_all[bear_daily_all["deviation"] <  HIGH_DEV_THRESHOLD]
    print(f"  Strategy B filter: {len(hd_bear)} high-dev exits ({len(hd_bear)/len(bear_daily_all)*100:.0f}%)"
          f" vs {len(ld_bear)} ignored low-dev")

    # C-specific: how many bear signals filtered by regime
    feats_map = dict(zip(feats["date"], feats["regime"]))
    bear_daily_all["_regime"] = bear_daily_all["trade_date"].map(feats_map)
    bear_bull = bear_daily_all[bear_daily_all["_regime"] == "bull"]
    print(f"  Strategy C filter: {len(bear_bull)} bull-regime bear signals ignored"
          f" (regime gate is NO-OP on this window - all signals in chop/bear)")
    print()


if __name__ == "__main__":
    main()
