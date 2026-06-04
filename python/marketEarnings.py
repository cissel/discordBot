#!/usr/bin/env python3
"""
marketEarnings.py
Usage:
  python marketEarnings.py          -- upcoming earnings for next 7 days (major tickers)
  python marketEarnings.py AAPL     -- earnings history + future dates for a specific ticker

Output (no-arg mode):  ~/discordBot/outputs/markets/earnings_upcoming.csv
Output (ticker mode):  ~/discordBot/outputs/markets/earnings_ticker.csv
"""

import sys
import csv
import os
from datetime import datetime, timedelta, timezone

import yfinance as yf

UPCOMING_PATH = os.path.expanduser("~/discordBot/outputs/markets/earnings_upcoming.csv")
TICKER_PATH   = os.path.expanduser("~/discordBot/outputs/markets/earnings_ticker.csv")

MAJOR_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM",  "BAC",  "GS",
    "MS",   "WMT",  "HD",   "NKE",  "DIS",
    "NFLX", "AMD",  "INTC", "QCOM", "MU",
]


def safe_val(val):
    """Convert NaN / None / pandas NA to empty string."""
    try:
        import math
        if val is None:
            return ""
        if isinstance(val, float) and math.isnan(val):
            return ""
        return val
    except Exception:
        return ""


def fetch_ticker_earnings(ticker_sym):
    """
    Fetch earnings_dates DataFrame for a single ticker.
    Returns the DataFrame or None if unavailable.
    """
    try:
        t = yf.Ticker(ticker_sym)
        df = t.earnings_dates
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def get_company_name(ticker_sym):
    """Best-effort fetch of the company short name from .info."""
    try:
        info = yf.Ticker(ticker_sym).info
        return info.get("shortName", ticker_sym)
    except Exception:
        return ticker_sym


# ---------------------------------------------------------------------------
# Mode 1: specific ticker
# ---------------------------------------------------------------------------

def run_ticker_mode(ticker_sym):
    df = fetch_ticker_earnings(ticker_sym)
    if df is None:
        print(f"error: no earnings data for {ticker_sym}")
        sys.exit(1)

    # earnings_dates index is tz-aware datetime; sort ascending
    df = df.sort_index()

    now = datetime.now(timezone.utc)

    # Split into past and future
    past   = df[df.index <= now]
    future = df[df.index >  now]

    # Last 4 past + next 4 future
    selected = []
    for idx, row in list(past.iterrows())[-4:]:
        selected.append((idx, row))
    for idx, row in list(future.iterrows())[:4]:
        selected.append((idx, row))

    os.makedirs(os.path.dirname(TICKER_PATH), exist_ok=True)
    with open(TICKER_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "eps_estimate", "reported_eps", "surprise_pct"])
        for dt, row in selected:
            date_str     = dt.strftime("%Y-%m-%d")
            eps_est      = safe_val(row.get("EPS Estimate"))
            reported_eps = safe_val(row.get("Reported EPS"))
            surprise     = safe_val(row.get("Surprise(%)"))
            writer.writerow([date_str, eps_est, reported_eps, surprise])

    print("ok")


# ---------------------------------------------------------------------------
# Mode 2: upcoming earnings (next 7 days, major tickers)
# ---------------------------------------------------------------------------

def run_upcoming_mode():
    now      = datetime.now(timezone.utc)
    week_end = now + timedelta(days=7)

    results = []  # list of (date, ticker, company_name, eps_estimate)

    for sym in MAJOR_TICKERS:
        df = fetch_ticker_earnings(sym)
        if df is None:
            continue
        for dt, row in df.iterrows():
            # Make dt tz-aware if needed
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if now <= dt <= week_end:
                eps_est = safe_val(row.get("EPS Estimate"))
                name    = ""  # skip per-ticker .info call to keep it fast
                results.append((dt, sym, name, eps_est))

    # Sort by date ascending
    results.sort(key=lambda x: x[0])

    # Back-fill company names only for matched tickers (avoid hitting .info 20 times)
    matched_syms = list(dict.fromkeys(r[1] for r in results))  # unique, order-preserved
    name_map = {}
    for sym in matched_syms:
        name_map[sym] = get_company_name(sym)

    os.makedirs(os.path.dirname(UPCOMING_PATH), exist_ok=True)
    with open(UPCOMING_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "ticker", "company_name", "eps_estimate"])
        for dt, sym, _, eps_est in results:
            writer.writerow([
                dt.strftime("%Y-%m-%d"),
                sym,
                name_map.get(sym, sym),
                eps_est,
            ])

    print("ok")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) >= 2:
        ticker_sym = sys.argv[1].upper().strip()
        run_ticker_mode(ticker_sym)
    else:
        run_upcoming_mode()


if __name__ == "__main__":
    main()
