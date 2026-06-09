#!/usr/bin/env python3
# regressCache.py - unified local cache for all regression regressors
#
# Each regressor is identified by a short KEY string (e.g. "SPY", "EUR_USD", "VIX").
# On first fetch the full history is written to outputs/markets/cache/<KEY>.csv
# On subsequent fetches only missing days are pulled and appended.
# Cache is considered fresh if it ends on today or yesterday (weekday logic).
#
# Usage (standalone refresh):
#   python3 regressCache.py [KEY [KEY ...]]   - refresh specific keys
#   python3 regressCache.py --all             - refresh all registered keys

import os
import sys
import json
import requests
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
FRED_KEY   = os.getenv("FRED_API_KEY", "d47e2b30bf4826314df23a57408a56a6").strip()

ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

CACHE_DIR = os.path.expanduser("~/discordBot/outputs/markets/cache/")
os.makedirs(CACHE_DIR, exist_ok=True)

today     = date.today()
today_utc = datetime.now(timezone.utc)

# ── regressor registry ─────────────────────────────────────────────────────────
# Each entry: key -> {type, label, fetch_args}
# type: alpaca_stock | alpaca_crypto | fred | frankfurter | btc_hashrate

REGRESSORS = {
    # Equities (Alpaca, log returns)
    "SPY":  {"type": "alpaca_stock",  "label": "S&P 500 (SPY)",          "ticker": "SPY"},
    "QQQ":  {"type": "alpaca_stock",  "label": "Nasdaq 100 (QQQ)",        "ticker": "QQQ"},
    "GLD":  {"type": "alpaca_stock",  "label": "Gold ETF (GLD)",          "ticker": "GLD"},
    "USO":  {"type": "alpaca_stock",  "label": "Oil ETF (USO)",           "ticker": "USO"},
    "IWM":  {"type": "alpaca_stock",  "label": "Russell 2000 (IWM)",      "ticker": "IWM"},
    "DIA":  {"type": "alpaca_stock",  "label": "Dow Jones (DIA)",         "ticker": "DIA"},
    # Crypto (Alpaca, log returns)
    "ETH":  {"type": "alpaca_crypto", "label": "Ethereum returns (ETH)",  "ticker": "ETH/USD"},
    "SOL":  {"type": "alpaca_crypto", "label": "Solana returns (SOL)",    "ticker": "SOL/USD"},
    # BTC on-chain
    "HASHRATE": {"type": "btc_hashrate", "label": "BTC Hashrate (log)"},
    # FRED macro (level values, forward-filled to daily)
    "VIX":      {"type": "fred", "label": "VIX (CBOE Volatility)",       "series": "VIXCLS"},
    "M2":       {"type": "fred", "label": "M2 Money Supply",             "series": "M2SL"},
    "UNRATE":   {"type": "fred", "label": "Unemployment Rate",           "series": "UNRATE"},
    "CPI":      {"type": "fred", "label": "CPI (All Urban)",             "series": "CPIAUCSL"},
    "T10Y2Y":   {"type": "fred", "label": "Yield Spread (10Y-2Y)",       "series": "T10Y2Y"},
    "FEDFUNDS": {"type": "fred", "label": "Fed Funds Rate",              "series": "FEDFUNDS"},
    "WTI":      {"type": "fred", "label": "WTI Crude Oil (FRED)",        "series": "DCOILWTICO"},
    "DXY":      {"type": "fred", "label": "US Dollar Index (DXY)",       "series": "DTWEXBGS"},
    "GOLD":     {"type": "fred", "label": "Gold Price (FRED)",           "series": "GOLDAMGBD228NLBM"},
    # FX (frankfurter.app, ECB rates vs USD, log returns)
    "EUR_USD": {"type": "frankfurter", "label": "EUR/USD",  "currency": "EUR"},
    "GBP_USD": {"type": "frankfurter", "label": "GBP/USD",  "currency": "GBP"},
    "JPY_USD": {"type": "frankfurter", "label": "JPY/USD",  "currency": "JPY"},
    "CNY_USD": {"type": "frankfurter", "label": "CNY/USD",  "currency": "CNY"},
    "CAD_USD": {"type": "frankfurter", "label": "CAD/USD",  "currency": "CAD"},
    "BRL_USD": {"type": "frankfurter", "label": "BRL/USD",  "currency": "BRL"},
    "MXN_USD": {"type": "frankfurter", "label": "MXN/USD",  "currency": "MXN"},
    "AUD_USD": {"type": "frankfurter", "label": "AUD/USD",  "currency": "AUD"},
    "CHF_USD": {"type": "frankfurter", "label": "CHF/USD",  "currency": "CHF"},
    "SEK_USD": {"type": "frankfurter", "label": "SEK/USD",  "currency": "SEK"},
    "INR_USD": {"type": "frankfurter", "label": "INR/USD",  "currency": "INR"},
    "KRW_USD": {"type": "frankfurter", "label": "KRW/USD",  "currency": "KRW"},
}

# column name for each key in the cached CSV
def col_name(key):
    reg = REGRESSORS[key]
    rtype = reg["type"]
    if rtype in ("alpaca_stock", "alpaca_crypto"):
        return f"{key}_ret"
    elif rtype == "btc_hashrate":
        return "HASHRATE_val"
    elif rtype == "fred":
        return f"{reg['series']}_val"
    elif rtype == "frankfurter":
        return f"{key}_ret"
    return f"{key}_val"


# ── fetch helpers ──────────────────────────────────────────────────────────────

def _alpaca_daily(ticker, asset_class, start_dt, end_dt):
    if asset_class == "crypto":
        url = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params = {"symbols": ticker, "timeframe": "1Day",
                  "start": start_dt.isoformat(), "end": end_dt.isoformat(), "limit": 10000}
    else:
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
        params = {"timeframe": "1Day", "start": start_dt.isoformat(), "end": end_dt.isoformat(),
                  "adjustment": "all", "feed": "sip", "limit": 10000}

    all_bars, next_page = [], None
    while True:
        if next_page:
            params["page_token"] = next_page
        r = requests.get(url, headers=ALPACA_HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca {r.status_code}: {r.text[:300]}")
        data = r.json()
        bars = data.get("bars", {}).get(ticker, []) if asset_class == "crypto" else data.get("bars", [])
        all_bars.extend(bars)
        next_page = data.get("next_page_token")
        if not next_page:
            break

    if not all_bars:
        return pd.DataFrame()
    df = pd.DataFrame(all_bars).rename(columns={"t": "date", "c": "close"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["date", "close"]].sort_values("date").reset_index(drop=True)


def _fred_series(series_id, start_date):
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
        "observation_start": str(start_date), "observation_end": str(today),
    }, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"FRED {r.status_code}: {r.text[:300]}")
    obs = [{"date": date.fromisoformat(o["date"]), "value": float(o["value"])}
           for o in r.json().get("observations", []) if o["value"] != "."]
    if not obs:
        return pd.DataFrame()
    df = pd.DataFrame(obs).sort_values("date").reset_index(drop=True)
    # forward-fill to every business day
    all_dates = pd.date_range(df["date"].min(), today, freq="B").date
    df_ff = pd.DataFrame({"date": all_dates}).merge(df, on="date", how="left").ffill()
    return df_ff.dropna()


def _frankfurter(currency, start_date):
    """Fetch full FX history from frankfurter.app in annual chunks to avoid huge responses."""
    rows = []
    chunk_start = start_date
    while chunk_start <= today:
        chunk_end = min(date(chunk_start.year + 1, chunk_start.month, chunk_start.day) - timedelta(days=1), today)
        r = requests.get(
            f"https://api.frankfurter.app/{chunk_start}..{chunk_end}",
            params={"from": "USD", "to": currency},
            timeout=20
        )
        if r.status_code != 200:
            raise RuntimeError(f"Frankfurter {r.status_code}: {r.text[:200]}")
        data = r.json()
        for d_str, rates in data.get("rates", {}).items():
            val = rates.get(currency)
            if val:
                rows.append({"date": date.fromisoformat(d_str), "value": float(val)})
        chunk_start = chunk_end + timedelta(days=1)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    # forward-fill to every calendar day for clean joins
    all_dates = pd.date_range(df["date"].min(), today, freq="D").date
    df_ff = pd.DataFrame({"date": all_dates}).merge(df, on="date", how="left").ffill().dropna()
    return df_ff


def _btc_hashrate(start_date):
    r = requests.get("https://api.blockchain.info/charts/hash-rate",
                     params={"timespan": "all", "format": "json", "sampled": "true"},
                     timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Blockchain.com {r.status_code}")
    rows = []
    for v in r.json().get("values", []):
        d = datetime.fromtimestamp(v["x"], tz=timezone.utc).date()
        if d >= start_date and v["y"] and v["y"] > 0:
            rows.append({"date": d, "value": float(np.log(v["y"]))})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    all_dates = pd.date_range(df["date"].min(), today, freq="D").date
    df_ff = pd.DataFrame({"date": all_dates}).merge(df, on="date", how="left").ffill().dropna()
    return df_ff[df_ff["date"] >= start_date].reset_index(drop=True)


# ── cache read/write ───────────────────────────────────────────────────────────

def cache_path(key):
    return os.path.join(CACHE_DIR, f"{key}.csv")


def _is_fresh(key):
    """Cache is fresh if last row date >= most recent weekday."""
    p = cache_path(key)
    if not os.path.exists(p):
        return False
    try:
        df = pd.read_csv(p, usecols=["date"])
        last = pd.to_datetime(df["date"].iloc[-1]).date()
        # most recent weekday
        ref = today
        while ref.weekday() >= 5:
            ref -= timedelta(days=1)
        # FX/hashrate have weekend data so allow today
        return last >= ref - timedelta(days=1)
    except Exception:
        return False


def load_cache(key):
    p = cache_path(key)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _save(key, df):
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df.to_csv(cache_path(key), index=False)


def _log_returns_col(df, price_col, out_col):
    df = df.copy()
    df[out_col] = np.log(df[price_col] / df[price_col].shift(1))
    return df.dropna(subset=[out_col])


# ── public API ─────────────────────────────────────────────────────────────────

def fetch_regressor(key, verbose=True):
    """
    Fetch or refresh a regressor by key.
    Returns a DataFrame with columns [date, <col_name(key)>].
    Raises RuntimeError on failure.
    """
    if key not in REGRESSORS:
        raise ValueError(f"Unknown regressor key: '{key}'. Available: {sorted(REGRESSORS.keys())}")

    reg   = REGRESSORS[key]
    rtype = reg["type"]
    col   = col_name(key)

    # check cache
    cached = load_cache(key)
    if cached is not None and _is_fresh(key):
        if verbose:
            print(f"[cache] {key}: {len(cached)} rows (fresh)", file=sys.stderr)
        return cached[["date", col]]

    # determine start date for fetch
    EARLIEST = {
        "alpaca_stock":  date(2016, 1, 1),
        "alpaca_crypto": date(2021, 1, 1),
        "btc_hashrate":  date(2014, 1, 1),
        "fred":          date(1990, 1, 1),
        "frankfurter":   date(1999, 1, 5),
    }

    if cached is not None and len(cached) > 0:
        # only fetch missing days
        last_cached = pd.to_datetime(cached["date"].iloc[-1]).date()
        start_date  = last_cached + timedelta(days=1)
        if verbose:
            print(f"[cache] {key}: patching from {start_date}", file=sys.stderr)
    else:
        start_date = EARLIEST.get(rtype, date(2016, 1, 1))
        if verbose:
            print(f"[cache] {key}: full fetch from {start_date}", file=sys.stderr)

    if start_date > today:
        if verbose:
            print(f"[cache] {key}: already current", file=sys.stderr)
        return cached[["date", col]] if cached is not None else pd.DataFrame(columns=["date", col])

    # fetch
    if rtype == "alpaca_stock":
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        raw = _alpaca_daily(reg["ticker"], "stocks", start_dt, today_utc)
        if raw.empty:
            raise RuntimeError(f"No Alpaca stock data for {reg['ticker']}")
        raw_col = _log_returns_col(raw, "close", col)
        new_df  = raw_col[["date", col]]

    elif rtype == "alpaca_crypto":
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        # check max_bars cache for BTC/ETH/DOGE
        coin = reg["ticker"].split("/")[0]
        max_cache = os.path.join(os.path.dirname(CACHE_DIR), f"{coin}_max_bars.csv")
        if os.path.exists(max_cache) and cached is None:
            raw2 = pd.read_csv(max_cache)
            raw2.columns = [c.lower() for c in raw2.columns]
            date_c = "date" if "date" in raw2.columns else "timestamp"
            raw2 = raw2.rename(columns={date_c: "date"})
            raw2["date"] = pd.to_datetime(raw2["date"]).dt.date
            raw2 = raw2.sort_values("date")[["date", "close"]]
        else:
            raw2 = _alpaca_daily(reg["ticker"], "crypto", start_dt, today_utc)
        if raw2.empty:
            raise RuntimeError(f"No data for {reg['ticker']}")
        raw_col = _log_returns_col(raw2, "close", col)
        new_df  = raw_col[["date", col]]

    elif rtype == "btc_hashrate":
        raw = _btc_hashrate(start_date)
        if raw.empty:
            raise RuntimeError("No hashrate data")
        new_df = raw.rename(columns={"value": col})[["date", col]]

    elif rtype == "fred":
        raw = _fred_series(reg["series"], start_date)
        if raw.empty:
            raise RuntimeError(f"No FRED data for {reg['series']}")
        new_df = raw.rename(columns={"value": col})[["date", col]]

    elif rtype == "frankfurter":
        raw = _frankfurter(reg["currency"], start_date)
        if raw.empty:
            raise RuntimeError(f"No FX data for {reg['currency']}")
        raw_col = _log_returns_col(raw, "value", col)
        new_df  = raw_col[["date", col]]

    else:
        raise RuntimeError(f"Unknown regressor type: {rtype}")

    # merge with existing cache and save
    if cached is not None and len(cached) > 0:
        combined = pd.concat([cached, new_df], ignore_index=True)
    else:
        combined = new_df

    combined["date"] = pd.to_datetime(combined["date"]).dt.date
    _save(key, combined)

    if verbose:
        print(f"[cache] {key}: saved {len(combined)} rows -> {cache_path(key)}", file=sys.stderr)

    return combined[["date", col]]


def fetch_target(target_type, symbol):
    """
    Fetch price series for the regression target.
    Returns DataFrame with [date, <SYMBOL>_ret].
    Uses existing max_bars cache when available.
    """
    col = f"{symbol.replace('/', '')}_ret"

    if target_type == "crypto":
        coin = symbol.split("/")[0].upper()
        max_cache = os.path.join(os.path.dirname(CACHE_DIR), f"{coin}_max_bars.csv")
        if os.path.exists(max_cache):
            age_h = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(max_cache))).total_seconds() / 3600
            if age_h < 25:
                raw = pd.read_csv(max_cache)
                raw.columns = [c.lower() for c in raw.columns]
                dc = "date" if "date" in raw.columns else "timestamp"
                raw = raw.rename(columns={dc: "date"})
                raw["date"] = pd.to_datetime(raw["date"]).dt.date
                raw = raw.sort_values("date")[["date", "close"]]
                return _log_returns_col(raw, "close", col)[["date", col]]

        # fallback: alpaca
        start_dt = datetime.combine(date(2021, 1, 1), datetime.min.time()).replace(tzinfo=timezone.utc)
        ticker = coin + "/USD"
        raw = _alpaca_daily(ticker, "crypto", start_dt, today_utc)
        if raw.empty:
            raise RuntimeError(f"No crypto data for {symbol}")
        return _log_returns_col(raw, "close", col)[["date", col]]

    elif target_type == "stocks":
        start_dt = datetime.combine(date(2016, 1, 1), datetime.min.time()).replace(tzinfo=timezone.utc)
        raw = _alpaca_daily(symbol, "stocks", start_dt, today_utc)
        if raw.empty:
            raise RuntimeError(f"No stock data for {symbol}")
        return _log_returns_col(raw, "close", col)[["date", col]]

    else:
        raise ValueError(f"Unknown target_type: {target_type}")


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--all" in args:
        keys = list(REGRESSORS.keys())
    elif args:
        keys = [a.upper() for a in args if a.startswith("--") is False]
    else:
        print("Usage: python3 regressCache.py [KEY ...] | --all")
        print("Available keys:", ", ".join(sorted(REGRESSORS.keys())))
        sys.exit(0)

    for key in keys:
        try:
            df = fetch_regressor(key, verbose=True)
            print(f"  {key}: {len(df)} rows, {df['date'].iloc[0]} to {df['date'].iloc[-1]}")
        except Exception as e:
            print(f"  {key}: ERROR - {e}", file=sys.stderr)
