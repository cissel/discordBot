#!/usr/bin/env python3
"""
crypto_grid_fetch.py
====================
Pull and cache Alpaca crypto bars for the grid study (crypto_grid_study.py).

  1Min  bars: last 12 months, for BTC/USD, ETH/USD, SOL/USD
  1Hour bars: full history (2021+), same symbols
  1Day  bars: full history, same symbols (needed for the 14-day ATR the live
              bot uses to size the grid; the live bot pulls 1Day bars from
              the same endpoint so this is the exact ATR input)

Cache: outputs/markets/cache/crypto/<SYM>_{1Min,1Hour,1Day}.csv.gz
Columns: t (UTC ISO), o, h, l, c, v, n, vw. Append-only resume: if a cache
exists, only bars after the last cached timestamp are fetched.

Read-only on the account (data endpoint only). Keys never printed.
"""
import os
import sys
import time
import gzip
import datetime as dt
import requests
import pandas as pd

sys.path.insert(0, "/home/jhcv/discordBot/python")
from marketMaker import load_env  # noqa: E402

DATA_BASE = "https://data.alpaca.markets"
CACHE_DIR = os.path.expanduser("~/discordBot/outputs/markets/cache/crypto")
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
COLS = ["t", "o", "h", "l", "c", "v", "n", "vw"]


def headers():
    env = load_env()
    return {"APCA-API-KEY-ID": env.get("APCA_PAPER_API_KEY_ID", "").strip(),
            "APCA-API-SECRET-KEY": env.get("APCA_PAPER_API_SECRET_KEY", "").strip()}


def cache_path(symbol, tf):
    return os.path.join(CACHE_DIR, f"{symbol.replace('/', '')}_{tf}.csv.gz")


def fetch(symbol, tf, start, end, hdr):
    rows = []
    token = None
    url = f"{DATA_BASE}/v1beta3/crypto/us/bars"
    while True:
        params = {"symbols": symbol, "timeframe": tf, "limit": 10000,
                  "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if token:
            params["page_token"] = token
        for attempt in range(6):
            try:
                r = requests.get(url, headers=hdr, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except requests.exceptions.RequestException:
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"fetch failed repeatedly for {symbol} {tf}")
        js = r.json()
        bars = (js.get("bars") or {}).get(symbol, [])
        rows.extend(bars)
        token = js.get("next_page_token")
        if not token:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLS)
    return df[COLS]


def update(symbol, tf, lookback_days, hdr):
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    start = now - dt.timedelta(days=lookback_days)
    p = cache_path(symbol, tf)
    old = None
    if os.path.exists(p):
        old = pd.read_csv(p)
        if not old.empty:
            last = pd.Timestamp(old["t"].iloc[-1])
            start = max(start, last.to_pydatetime() + dt.timedelta(seconds=1))
    if start >= now:
        print(f"{symbol} {tf}: cache current ({len(old)} rows)")
        return
    new = fetch(symbol, tf, start, now, hdr)
    df = pd.concat([old, new], ignore_index=True) if old is not None else new
    df = df.drop_duplicates("t").sort_values("t").reset_index(drop=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(p, index=False, compression="gzip")
    print(f"{symbol} {tf}: +{len(new)} new, {len(df)} total, {df['t'].iloc[0]} .. {df['t'].iloc[-1]}")


if __name__ == "__main__":
    hdr = headers()
    if not hdr["APCA-API-KEY-ID"]:
        sys.exit("no Alpaca keys in .env")
    for sym in SYMBOLS:
        update(sym, "1Day", 365 * 6, hdr)
        update(sym, "1Hour", 365 * 6, hdr)
        update(sym, "1Min", 366, hdr)
