#!/usr/bin/env python3
"""
alpacaFees.py
Cheap, no-API accessor for broker fees per strategy / symbol, read from the
daily pnlLedger.py output (outputs/markets/pnl_ledger.csv). Alpaca PAPER
charges real fees (REG/TAF/CAT on stock sells, crypto pair fees in USD and
in-coin); every paper P&L number the bot reports must be net of them.

The ledger refreshes at 16:55 daily, so intraday callers see fees as of the
last run (noted via `as_of`). That lag is accepted to avoid every 1-minute
bot cycle hitting the activities API.

    from alpacaFees import strategy_fees, fees_for_symbol
    strategy_fees()            -> {"Grid BTC": -17.2, "SPY autopilot": -0.96, ...}, as_of
    fees_for_symbol("BTC/USD") -> -17.2
"""
import os
import csv
import datetime as dt

LEDGER_CSV = os.path.expanduser("~/discordBot/outputs/markets/pnl_ledger.csv")

SYMBOL_TO_STRATEGY = {
    "SPY": "SPY autopilot", "SOXL": "MM SOXL", "TQQQ": "MM TQQQ (RL)", "GME": "MM GME",
    "BTC/USD": "Grid BTC", "ETH/USD": "Grid ETH", "SOL/USD": "Grid SOL",
    "BTCUSD": "Grid BTC", "ETHUSD": "Grid ETH", "SOLUSD": "Grid SOL",
}


def strategy_fees():
    """Returns ({strategy: fees_usd (negative)}, as_of_iso_or_None)."""
    if not os.path.exists(LEDGER_CSV):
        return {}, None
    out = {}
    with open(LEDGER_CSV) as f:
        for r in csv.DictReader(f):
            try:
                out[r["strategy"]] = float(r.get("fees") or 0.0)
            except (TypeError, ValueError):
                pass
    as_of = dt.datetime.fromtimestamp(os.path.getmtime(LEDGER_CSV)).isoformat(timespec="minutes")
    return out, as_of


def fees_for_symbol(symbol):
    fees, _ = strategy_fees()
    return fees.get(SYMBOL_TO_STRATEGY.get(symbol, symbol), 0.0)
