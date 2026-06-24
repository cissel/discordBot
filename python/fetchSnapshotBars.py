#!/usr/bin/env python3
"""
fetchSnapshotBars.py  -  fetch price bars for /snapshot command
================================================================
Fetches equity/bond ETF bars from Alpaca (multi-symbol) and FX rates
from Frankfurter (ECB), then writes per-symbol CSVs.

Usage:
  python3 fetchSnapshotBars.py <timeframe>

Timeframes: intraday | 1w | 1mo | 3mo | 6mo | 1y

Output files (in outputs/markets/snapshot/):
  <SYMBOL>_<timeframe>.csv   columns: date, open, high, low, close, volume
  FX_<PAIR>_<timeframe>.csv  columns: date, rate  (FX only, no volume)
"""

import os
import sys
import requests
import pandas as pd
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

if not API_KEY or not API_SECRET:
    print("ERROR: Alpaca API keys not found in .env", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# ── tickers ───────────────────────────────────────────────────────────────────
# Row 1 - equities
EQUITY_SYMBOLS = ["SPY", "QQQ", "DIA", "IWM"]
# Row 2 - bonds (short to long, then credit)
BOND_SYMBOLS   = ["SHY", "IEF", "TLT", "HYG"]
ALL_ETF_SYMS   = EQUITY_SYMBOLS + BOND_SYMBOLS

# Row 3 - FX pairs (Frankfurter) + DXY proxy (Alpaca)
FX_PAIRS = [
    {"pair": "EUR/USD", "base": "EUR", "target": "USD"},
    {"pair": "GBP/USD", "base": "GBP", "target": "USD"},
    {"pair": "USD/JPY", "base": "USD", "target": "JPY"},
]
DXY_SYMBOL = "UUP"   # Invesco USD Index Bullish ETF - proxy for DXY on Alpaca

ET = ZoneInfo("America/New_York")

OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets/snapshot")
os.makedirs(OUT_DIR, exist_ok=True)

# ── timeframe map ─────────────────────────────────────────────────────────────
timeframe = sys.argv[1].lower() if len(sys.argv) > 1 else "1mo"
today = date.today()

TIMEFRAME_MAP = {
    "intraday": (today,                        today,  "1Min"),
    "1w":       (today - timedelta(weeks=1),   today,  "5Min"),
    "1mo":      (today - timedelta(days=30),   today,  "1Day"),
    "3mo":      (today - timedelta(days=90),   today,  "1Day"),
    "6mo":      (today - timedelta(days=182),  today,  "1Day"),
    "1y":       (today - timedelta(days=365),  today,  "1Day"),
}

if timeframe not in TIMEFRAME_MAP:
    print(f"ERROR: Unknown timeframe '{timeframe}'. Choose: {', '.join(TIMEFRAME_MAP)}", file=sys.stderr)
    sys.exit(1)

start_date, end_date, bar_tf = TIMEFRAME_MAP[timeframe]

# ── for intraday: step back to last trading day ───────────────────────────────
if timeframe == "intraday":
    check_date = today
    for _ in range(7):
        if check_date.weekday() < 5:
            test_resp = requests.get(
                "https://data.alpaca.markets/v2/stocks/SPY/bars",
                headers=HEADERS,
                params={
                    "timeframe": "1Min",
                    "start": check_date.isoformat(),
                    "end":   check_date.isoformat(),
                    "feed":  "sip",
                    "limit": 1,
                },
                timeout=15,
            )
            if test_resp.status_code == 200 and test_resp.json().get("bars"):
                break
        check_date -= timedelta(days=1)
    start_date = end_date = check_date


# ── fetch multi-symbol ETF bars from Alpaca ───────────────────────────────────
def fetch_etf_bars(symbols):
    """Return {symbol: DataFrame} with columns date, open, high, low, close, volume."""
    url = "https://data.alpaca.markets/v2/stocks/bars"
    params = {
        "symbols":    ",".join(symbols),
        "timeframe":  bar_tf,
        "start":      start_date.isoformat(),
        "end":        end_date.isoformat(),
        "adjustment": "all",
        "feed":       "sip",
        "limit":      10000,
    }

    all_bars = {s: [] for s in symbols}
    next_page = None

    while True:
        if next_page:
            params["page_token"] = next_page
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"ERROR: Alpaca multi-bar {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()
        bars_dict = data.get("bars", {})
        for sym, bars in bars_dict.items():
            all_bars[sym].extend(bars)
        next_page = data.get("next_page_token")
        if not next_page:
            break

    result = {}
    for sym in symbols:
        raw = all_bars.get(sym, [])
        if not raw:
            print(f"WARNING: No data for {sym}", file=sys.stderr)
            continue
        df = pd.DataFrame(raw).rename(columns={
            "t": "date", "o": "open", "h": "high",
            "l": "low",  "c": "close", "v": "volume"
        })
        df["date"] = pd.to_datetime(df["date"])
        if bar_tf in ("1Min", "5Min"):
            df["date"] = df["date"].dt.tz_convert("America/New_York")
        else:
            df["date"] = df["date"].dt.date
        df = df.sort_values("date").reset_index(drop=True)
        result[sym] = df
    return result


print(f"Fetching ETF bars ({timeframe}) for: {', '.join(ALL_ETF_SYMS + [DXY_SYMBOL])}")
etf_data = fetch_etf_bars(ALL_ETF_SYMS + [DXY_SYMBOL])

for sym, df in etf_data.items():
    out = os.path.join(OUT_DIR, f"{sym}_{timeframe}.csv")
    df.to_csv(out, index=False)
    print(f"  Saved {len(df)} rows -> {out}")

# ── fetch FX from Frankfurter (ECB, free) ────────────────────────────────────
# Frankfurter is daily only - for intraday/1w we grab the last 30 days of daily
def fetch_fx_daily(base, target):
    """Fetch daily rates base/target from Frankfurter."""
    fx_start = start_date if timeframe not in ("intraday", "1w") else (today - timedelta(days=30))
    url = (
        f"https://api.frankfurter.dev/v1/"
        f"{fx_start.isoformat()}..{today.isoformat()}"
        f"?from={base}"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if resp.status_code != 200:
            print(f"WARNING: Frankfurter {base}/{target} HTTP {resp.status_code}", file=sys.stderr)
            return None
        dat = resp.json()
        rates_raw = dat.get("rates", {})
        if not rates_raw:
            return None
        rows = []
        for d, r in rates_raw.items():
            val = r.get(target)
            if val is not None:
                rows.append({"date": d, "rate": float(val)})
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"WARNING: FX fetch error {base}/{target}: {e}", file=sys.stderr)
        return None


print(f"\nFetching FX rates from Frankfurter...")
for pd_info in FX_PAIRS:
    pair  = pd_info["pair"]
    base  = pd_info["base"]
    tgt   = pd_info["target"]
    safe  = pair.replace("/", "_")
    df_fx = fetch_fx_daily(base, tgt)
    if df_fx is not None and len(df_fx) > 0:
        out = os.path.join(OUT_DIR, f"FX_{safe}_{timeframe}.csv")
        df_fx.to_csv(out, index=False)
        print(f"  Saved {len(df_fx)} rows -> {out}")
    else:
        print(f"  WARNING: No data for {pair}", file=sys.stderr)

print("\nfetchSnapshotBars.py complete.")
