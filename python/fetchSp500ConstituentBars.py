#!/usr/bin/env python3
"""
fetchSp500ConstituentBars.py
===============================
Bulk-fetch daily OHLCV bars for ALL current S&P 500 constituents from
Alpaca (multi-symbol bars endpoint, batched to stay well under the
documented 10,000 req/min rate limit -- ~503 tickers needs only a
handful of batched calls, with pagination per batch since 10yrs x 100
tickers can exceed the 10,000-bar-per-response limit).

CAVEAT (important, flagged up front): this uses the CURRENT constituent
list (outputs/markets/cache/sp500_constituents.csv, fetched by
sp500Heatmap.R from the GitHub datasets/s-and-p-500-companies list) for
the ENTIRE historical window. This is "survivorship-biased" by
construction -- a ticker that was removed from the index in 2019 (e.g.
acquired, delisted, or dropped for underperformance) won't be in this
fetch at all, even for the 2016-2019 period when it WAS a constituent.
For a pure "breadth of the index as it existed at the time" signal this
is a real bias (breadth will look artificially strong in the past,
since the survivors' history is systematically the history of
companies that didn't get kicked out). Acceptable for a first spike to
test whether breadth features have ANY signal at all; would need a
point-in-time constituent history (harder to source for free) before
trusting any absolute historical breadth LEVEL, as opposed to its
day-to-day CHANGE, which is less affected by this bias.

Output: outputs/markets/sp500_constituent_bars/{TICKER}.csv (503 files)
        + outputs/markets/sp500_constituent_bars_manifest.csv (fetch log)

Usage:
  venv/bin/python3 python/fetchSp500ConstituentBars.py
"""
import os
import sys
import time
import requests
import pandas as pd
from datetime import date
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
if not API_KEY or not API_SECRET:
    print("ERROR: Alpaca API keys not found in .env", file=sys.stderr)
    sys.exit(1)

HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
CONSTITUENTS_PATH = os.path.expanduser("~/discordBot/outputs/markets/cache/sp500_constituents.csv")
OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets/sp500_constituent_bars")
MANIFEST_PATH = os.path.expanduser("~/discordBot/outputs/markets/sp500_constituent_bars_manifest.csv")

START_DATE = "2016-01-01"   # matches spy_features.csv's history start
END_DATE = date.today().isoformat()
BATCH_SIZE = 50             # tickers per multi-symbol request
URL = "https://data.alpaca.markets/v2/stocks/bars"


def fetch_batch(symbols, start, end):
    """Fetch one batch of symbols, paginating within the batch as needed.
    A single invalid/delisted ticker in the batch makes Alpaca reject the
    WHOLE request (400) -- on any non-200, fall back to one-by-one fetches
    for this batch so one bad symbol doesn't lose the other ~49."""
    all_bars = {s: [] for s in symbols}
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start,
        "end": end,
        "adjustment": "all",
        "feed": "sip",
        "limit": 10000,
    }
    next_page = None
    while True:
        if next_page:
            params["page_token"] = next_page
        resp = requests.get(URL, headers=HEADERS, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  WARN: batch request failed ({resp.status_code}): {resp.text[:200]}")
            print(f"  Falling back to one-by-one fetch for this batch of {len(symbols)}...")
            return _fetch_one_by_one(symbols, start, end)
        data = resp.json()
        bars_by_symbol = data.get("bars", {})
        for sym, bars in bars_by_symbol.items():
            all_bars.setdefault(sym, []).extend(bars)
        next_page = data.get("next_page_token")
        if not next_page:
            break
    return all_bars


def _fetch_one_by_one(symbols, start, end):
    all_bars = {}
    for sym in symbols:
        params = {"symbols": sym, "timeframe": "1Day", "start": start, "end": end,
                   "adjustment": "all", "feed": "sip", "limit": 10000}
        bars = []
        next_page = None
        while True:
            if next_page:
                params["page_token"] = next_page
            resp = requests.get(URL, headers=HEADERS, params=params, timeout=30)
            if resp.status_code != 200:
                print(f"    skip {sym}: {resp.status_code} {resp.text[:100]}")
                break
            data = resp.json()
            bars.extend(data.get("bars", {}).get(sym, []))
            next_page = data.get("next_page_token")
            if not next_page:
                break
        all_bars[sym] = bars
        time.sleep(0.05)
    return all_bars


def main():
    constituents = pd.read_csv(CONSTITUENTS_PATH)
    symbols = sorted(constituents["symbol"].dropna().unique().tolist())
    # Alpaca accepts the literal dotted class-share ticker (e.g. "BRK.B")
    # as-is -- no normalization needed (confirmed via smoke test; dash/
    # slash variants are rejected). Kept as identity map for clarity and
    # as a hook if a future ticker needs special-casing.
    symbol_map = {s: s for s in symbols}
    print(f"Loaded {len(symbols)} S&P 500 constituents from {CONSTITUENTS_PATH}")
    print(f"Fetching daily bars {START_DATE} -> {END_DATE} in batches of {BATCH_SIZE}")

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest_rows = []
    batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]

    for bi, batch in enumerate(batches):
        alpaca_symbols = [symbol_map[s] for s in batch]
        print(f"  Batch {bi+1}/{len(batches)}: {alpaca_symbols[0]}..{alpaca_symbols[-1]} ({len(batch)} tickers)")
        t0 = time.time()
        bars_dict = fetch_batch(alpaca_symbols, START_DATE, END_DATE)
        dt = time.time() - t0

        for orig_sym, alpaca_sym in zip(batch, alpaca_symbols):
            bars = bars_dict.get(alpaca_sym, [])
            if not bars:
                manifest_rows.append({"symbol": orig_sym, "alpaca_symbol": alpaca_sym,
                                       "n_bars": 0, "status": "no_data"})
                continue
            df = pd.DataFrame(bars)
            df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                                     "l": "low", "c": "close", "v": "volume"})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date").reset_index(drop=True)
            df["symbol"] = orig_sym
            out_path = os.path.join(OUT_DIR, f"{orig_sym}.csv")
            df.to_csv(out_path, index=False)
            manifest_rows.append({"symbol": orig_sym, "alpaca_symbol": alpaca_sym,
                                   "n_bars": len(df), "status": "ok",
                                   "date_min": str(df["date"].min()), "date_max": str(df["date"].max())})
        print(f"    done in {dt:.1f}s")
        time.sleep(0.2)   # polite pacing, well under rate limit regardless

    manifest = pd.DataFrame(manifest_rows)
    manifest.to_csv(MANIFEST_PATH, index=False)
    n_ok = (manifest["status"] == "ok").sum()
    n_fail = (manifest["status"] != "ok").sum()
    print(f"\nDone. {n_ok}/{len(manifest)} tickers fetched OK, {n_fail} failed/no-data.")
    if n_fail > 0:
        print("Failed tickers:", manifest[manifest["status"] != "ok"]["symbol"].tolist())
    print(f"Manifest saved -> {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
