#!/usr/bin/env python3
"""
marketMovers.py
Fetches today's top 5 gainers and top 5 losers from Yahoo Finance.
Output:
  ~/discordBot/outputs/markets/gainers.csv
  ~/discordBot/outputs/markets/losers.csv
"""

import sys
import csv
import os

import yfinance as yf

GAINERS_PATH = os.path.expanduser("~/discordBot/outputs/markets/gainers.csv")
LOSERS_PATH  = os.path.expanduser("~/discordBot/outputs/markets/losers.csv")

FIELDNAMES = ["symbol", "name", "price", "change_pct"]


def extract_top5(screen_result):
    """
    Pull the first 5 entries from a yf.screen() result.
    Returns a list of dicts with keys: symbol, name, price, change_pct.
    """
    quotes = screen_result.get("quotes", [])
    rows = []
    for q in quotes[:5]:
        rows.append({
            "symbol":     q.get("symbol", ""),
            "name":       q.get("shortName", ""),
            "price":      round(q.get("regularMarketPrice", 0.0), 4),
            "change_pct": round(q.get("regularMarketChangePercent", 0.0), 4),
        })
    return rows


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    try:
        gainers_raw = yf.screen("day_gainers")
        losers_raw  = yf.screen("day_losers")

        gainers = extract_top5(gainers_raw)
        losers  = extract_top5(losers_raw)

        write_csv(GAINERS_PATH, gainers)
        write_csv(LOSERS_PATH,  losers)

        print("ok")

    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
