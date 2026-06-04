#!/usr/bin/env python3
"""
marketShort.py
Fetches short interest data for a given ticker.
Usage: python marketShort.py <TICKER>
Output: ~/discordBot/outputs/markets/short.csv
"""

import sys
import csv
import os
from datetime import datetime, timezone

import yfinance as yf

OUTPUT_PATH = os.path.expanduser("~/discordBot/outputs/markets/short.csv")

FIELDNAMES = [
    "ticker",
    "short_float_pct",
    "shares_short",
    "shares_float",
    "days_to_cover",
    "date_short_interest",
    "avg_volume",
    "price",
]


def safe_get(d, key, default=None):
    val = d.get(key, default)
    return val if val is not None else default


def format_date(unix_ts):
    """Convert a unix timestamp (int/float) to YYYY-MM-DD string, or empty string."""
    if not unix_ts:
        return ""
    try:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return str(unix_ts)


def main():
    if len(sys.argv) < 2:
        print("error: no ticker provided. Usage: marketShort.py <TICKER>")
        sys.exit(1)

    ticker_sym = sys.argv[1].upper().strip()

    try:
        t = yf.Ticker(ticker_sym)

        # ---- .info fields ----
        info = t.info

        short_pct_float   = safe_get(info, "shortPercentOfFloat")
        # Multiply by 100 only if value looks like a decimal (e.g. 0.05 -> 5%)
        if short_pct_float is not None:
            if short_pct_float < 1.0:          # stored as fraction
                short_pct_float = round(short_pct_float * 100, 4)
            else:                              # already a percentage
                short_pct_float = round(float(short_pct_float), 4)

        shares_short      = safe_get(info, "sharesShort")
        shares_float      = safe_get(info, "sharesFloat")
        days_to_cover     = safe_get(info, "shortRatio")
        date_short_int_ts = safe_get(info, "dateShortInterest")
        date_short_int    = format_date(date_short_int_ts)

        # ---- Recent price & volume from history ----
        hist = t.history(period="5d")

        if hist is not None and not hist.empty:
            avg_volume = round(hist["Volume"].mean(), 0)
            price      = round(float(hist["Close"].iloc[-1]), 4)
        else:
            avg_volume = None
            price      = safe_get(info, "regularMarketPrice")

        row = {
            "ticker":              ticker_sym,
            "short_float_pct":     short_pct_float if short_pct_float is not None else "",
            "shares_short":        shares_short    if shares_short    is not None else "",
            "shares_float":        shares_float    if shares_float    is not None else "",
            "days_to_cover":       days_to_cover   if days_to_cover   is not None else "",
            "date_short_interest": date_short_int,
            "avg_volume":          avg_volume      if avg_volume      is not None else "",
            "price":               price           if price           is not None else "",
        }

        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerow(row)

        print("ok")

    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
