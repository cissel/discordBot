#!/usr/bin/env python3
# fetchForecastData.py - fetch data for /markets forecast command
# Usage: python3 fetchForecastData.py <category> <symbol> <timeframe> <horizon>
#
# category : stocks | crypto | economic
# symbol   : AAPL, BTC/USD, CPIAUCSL, etc.
# timeframe: 1min | 5min | 15min | 1h | 1d   (bar granularity for stocks/crypto)
#            ignored for economic (always monthly/weekly from FRED)
# horizon  : 1h | 4h | 1d | 1w | 1mo | 3mo | 6mo | 1yr
#
# Outputs CSV to outputs/markets/forecast_<symbol>_<timeframe>_<horizon>.csv
# Prints JSON metadata to stdout for the bot to consume.

import os
import sys
import json
import requests
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

# ── argument parsing ──────────────────────────────────────────────────────────

if len(sys.argv) < 5:
    print("ERROR: Usage: fetchForecastData.py <category> <symbol> <timeframe> <horizon> [model]",
          file=sys.stderr)
    sys.exit(1)

category  = sys.argv[1].lower()
symbol    = sys.argv[2].upper()
timeframe = sys.argv[3].lower()
horizon   = sys.argv[4].lower()
model_arg = sys.argv[5].lower() if len(sys.argv) > 5 else "auto"

# ── validation ────────────────────────────────────────────────────────────────

VALID_TIMEFRAMES = ["1min", "5min", "15min", "1h", "1d"]
VALID_HORIZONS   = ["1h", "4h", "1d", "1w", "1mo", "3mo", "6mo", "1yr"]
VALID_CATEGORIES = ["stocks", "crypto", "economic"]

if category not in VALID_CATEGORIES:
    print(f"ERROR: category must be one of {VALID_CATEGORIES}", file=sys.stderr)
    sys.exit(1)

if category in ("stocks", "crypto") and timeframe not in VALID_TIMEFRAMES:
    print(f"ERROR: timeframe must be one of {VALID_TIMEFRAMES}", file=sys.stderr)
    sys.exit(1)

if horizon not in VALID_HORIZONS:
    print(f"ERROR: horizon must be one of {VALID_HORIZONS}", file=sys.stderr)
    sys.exit(1)

# ── guard: don't let people ask for long horizons on short timeframes ─────────
# intraday horizons only make sense on intraday bars
INTRADAY_TF = ["1min", "5min", "15min", "1h"]
INTRADAY_HZ = ["1h", "4h", "1d"]
DAILY_HZ    = ["1w", "1mo", "3mo", "6mo", "1yr"]

if category in ("stocks", "crypto"):
    if timeframe in INTRADAY_TF and horizon in DAILY_HZ:
        print(f"ERROR: horizon '{horizon}' is too long for intraday timeframe '{timeframe}'. "
              f"Use 1d bar timeframe for daily+ horizons.", file=sys.stderr)
        sys.exit(1)
    if timeframe == "1d" and horizon in INTRADAY_HZ:
        print(f"ERROR: horizon '{horizon}' is too short for daily bar timeframe '{timeframe}'. "
              f"Use an intraday timeframe for intraday horizons.", file=sys.stderr)
        sys.exit(1)

# ── map timeframe + horizon to Alpaca bar size and lookback ───────────────────

ALPACA_BAR_MAP = {
    "1min":  "1Min",
    "5min":  "5Min",
    "15min": "15Min",
    "1h":    "1Hour",
    "1d":    "1Day",
}

# how many bars of history to pull for model training
# rule of thumb: want at least 500 bars, more for daily
HISTORY_BARS = {
    "1min":  {"1h": 1,  "4h": 5,   "1d": 30},   # days of history
    "5min":  {"1h": 3,  "4h": 10,  "1d": 60},
    "15min": {"1h": 5,  "4h": 20,  "1d": 90},
    "1h":    {"1h": 30, "4h": 60,  "1d": 180},
    "1d":    {"1w": 365, "1mo": 730, "3mo": 1095, "6mo": 1460, "1yr": 1825},
}

# how many bars forward to forecast
HORIZON_BARS = {
    "1min":  {"1h": 60,  "4h": 240,  "1d": 390},
    "5min":  {"1h": 12,  "4h": 48,   "1d": 78},
    "15min": {"1h": 4,   "4h": 16,   "1d": 26},
    "1h":    {"1h": 1,   "4h": 4,    "1d": 8},
    "1d":    {"1w": 5,   "1mo": 21,  "3mo": 63, "6mo": 126, "1yr": 252},
}

# ── fetch stock bars from Alpaca ──────────────────────────────────────────────

def fetch_alpaca_bars(ticker, bar_tf, start_dt, end_dt, asset_class="stocks"):
    if asset_class == "crypto":
        url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params = {
            "symbols":   ticker,
            "timeframe": bar_tf,
            "start":     start_dt.isoformat(),
            "end":       end_dt.isoformat(),
            "limit":     10000,
        }
    else:
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
        params = {
            "timeframe":  bar_tf,
            "start":      start_dt.isoformat(),
            "end":        end_dt.isoformat(),
            "adjustment": "all",
            "feed":       "sip",
            "limit":      10000,
        }

    all_bars = []
    next_page = None

    while True:
        if next_page:
            params["page_token"] = next_page
        resp = requests.get(url, headers=ALPACA_HEADERS, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"ERROR: Alpaca API {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()

        if asset_class == "crypto":
            bars = data.get("bars", {}).get(ticker, [])
        else:
            bars = data.get("bars", [])

        all_bars.extend(bars)
        next_page = data.get("next_page_token")
        if not next_page:
            break

    return all_bars

# ── fetch FRED series ─────────────────────────────────────────────────────────

# Common FRED series aliases users might type
FRED_ALIASES = {
    "CPI":          "CPIAUCSL",
    "UNEMPLOYMENT": "UNRATE",
    "UNEMPLOYMENT_RATE": "UNRATE",
    "NONFARM":      "PAYEMS",
    "NFPAYROLLS":   "PAYEMS",
    "PAYROLLS":     "PAYEMS",
    "FEDFUNDS":     "FEDFUNDS",
    "FED":          "FEDFUNDS",
    "FEDRATE":      "FEDFUNDS",
    "T10Y2Y":       "T10Y2Y",
    "YIELDSPREAD":  "T10Y2Y",
    "T10Y3M":       "T10Y3M",
    "OIL":          "DCOILWTICO",
    "WTI":          "DCOILWTICO",
    "CRUDE":        "DCOILWTICO",
    "GOLD":         "GOLDAMGBD228NLBM",
    "M2":           "M2SL",
    "GDP":          "GDPC1",
    "PCE":          "PCEPI",
    "HOUSING":      "UNDCONTNSA",
    "HOUSINGUNITS": "UNDCONTNSA",
    "RETAIL":       "RSXFS",
    "INDUSTRIAL":   "INDPRO",
    "VIX":          "VIXCLS",
    "DOLLAR":       "DTWEXBGS",
    "DXY":          "DTWEXBGS",
    "BREAKEVEN":    "T10YIE",
    "TIPS":         "T10YIE",
}

def fetch_fred(series_id, observation_start="1990-01-01"):
    resolved = FRED_ALIASES.get(series_id.upper(), series_id)
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id":          resolved,
        "api_key":            FRED_KEY,
        "file_type":          "json",
        "observation_start":  observation_start,
        "observation_end":    date.today().isoformat(),
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"ERROR: FRED API {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    obs = data.get("observations", [])
    if not obs:
        print(f"ERROR: No FRED data for series '{resolved}'", file=sys.stderr)
        sys.exit(1)

    # also fetch series metadata for display name
    meta_url = "https://api.stlouisfed.org/fred/series"
    meta = requests.get(meta_url, params={
        "series_id": resolved,
        "api_key":   FRED_KEY,
        "file_type": "json",
    }, timeout=15).json()
    series_name = meta.get("seriess", [{}])[0].get("title", resolved)

    return obs, resolved, series_name

# ── main logic ────────────────────────────────────────────────────────────────

today_utc = datetime.now(timezone.utc)
today     = date.today()
meta      = {}

out_dir = os.path.expanduser("~/discordBot/outputs/markets/")
os.makedirs(out_dir, exist_ok=True)

safe_symbol = symbol.replace("/", "")
out_path    = os.path.join(out_dir, f"forecast_{safe_symbol}_{timeframe}_{horizon}.csv")

# ── STOCKS ────────────────────────────────────────────────────────────────────

if category == "stocks":
    if not API_KEY or not API_SECRET:
        print("ERROR: Alpaca API keys not found in .env", file=sys.stderr)
        sys.exit(1)

    bar_tf          = ALPACA_BAR_MAP[timeframe]
    history_days    = HISTORY_BARS[timeframe][horizon]
    horizon_bars    = HORIZON_BARS[timeframe][horizon]

    # for intraday, start from today; for daily, go back by history_days
    if timeframe in INTRADAY_TF:
        # find most recent trading day
        check = today
        for _ in range(10):
            if check.weekday() < 5:
                test = requests.get(
                    "https://data.alpaca.markets/v2/stocks/SPY/bars",
                    headers=ALPACA_HEADERS,
                    params={"timeframe":"1Min","start":check.isoformat(),"end":check.isoformat(),"feed":"sip","limit":1},
                    timeout=10
                )
                if test.status_code == 200 and test.json().get("bars"):
                    break
            check -= timedelta(days=1)
        start_dt = datetime.combine(check - timedelta(days=history_days), datetime.min.time()).replace(tzinfo=timezone.utc)
        end_dt   = today_utc
    else:
        start_dt = datetime.combine(today - timedelta(days=history_days), datetime.min.time()).replace(tzinfo=timezone.utc)
        end_dt   = today_utc

    bars = fetch_alpaca_bars(symbol, bar_tf, start_dt, end_dt, asset_class="stocks")

    if not bars:
        print(f"ERROR: No data returned for {symbol}", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(bars)
    df = df.rename(columns={"t":"timestamp","o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if timeframe in INTRADAY_TF:
        df["timestamp"] = df["timestamp"].dt.tz_convert("America/New_York")
    else:
        df["timestamp"] = df["timestamp"].dt.date.astype(str)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df.to_csv(out_path, index=False)

    meta = {
        "category":      "stocks",
        "symbol":        symbol,
        "timeframe":     timeframe,
        "horizon":       horizon,
        "horizon_bars":  horizon_bars,
        "bar_tf":        bar_tf,
        "n_bars":        len(df),
        "last_close":    float(df["close"].iloc[-1]),
        "last_time":     str(df["timestamp"].iloc[-1]),
        "out_path":      out_path,
        "model":         "NNETAR" if model_arg == "nnetar" else "GJR-GARCH(1,1)-t",
        "dist":          "student-t",
        "mc_sims":       0 if model_arg == "nnetar" else 500,
    }

# ── CRYPTO ────────────────────────────────────────────────────────────────────

elif category == "crypto":
    if not API_KEY or not API_SECRET:
        print("ERROR: Alpaca API keys not found in .env", file=sys.stderr)
        sys.exit(1)

    # Alpaca crypto format: BTC/USD, ETH/USD, etc.
    # User might type BTC, BTCUSD, BTC/USD - normalize
    if "/" not in symbol:
        if symbol.endswith("USD"):
            symbol = symbol[:-3] + "/USD"
        elif symbol.endswith("USDT"):
            symbol = symbol[:-4] + "/USD"
        else:
            symbol = symbol + "/USD"

    safe_symbol = symbol.replace("/", "")

    bar_tf       = ALPACA_BAR_MAP[timeframe]
    history_days = HISTORY_BARS[timeframe][horizon]
    horizon_bars = HORIZON_BARS[timeframe][horizon]

    start_dt = datetime.combine(today - timedelta(days=history_days), datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt   = today_utc

    bars = fetch_alpaca_bars(symbol, bar_tf, start_dt, end_dt, asset_class="crypto")

    if not bars:
        print(f"ERROR: No crypto data returned for {symbol}", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(bars)
    df = df.rename(columns={"t":"timestamp","o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["timestamp"] = df["timestamp"].dt.tz_convert("America/New_York")
    df = df.sort_values("timestamp").reset_index(drop=True)

    out_path = os.path.join(out_dir, f"forecast_{safe_symbol}_{timeframe}_{horizon}.csv")
    df.to_csv(out_path, index=False)

    meta = {
        "category":      "crypto",
        "symbol":        symbol,
        "safe_symbol":   safe_symbol,
        "timeframe":     timeframe,
        "horizon":       horizon,
        "horizon_bars":  horizon_bars,
        "bar_tf":        bar_tf,
        "n_bars":        len(df),
        "last_close":    float(df["close"].iloc[-1]),
        "last_time":     str(df["timestamp"].iloc[-1]),
        "out_path":      out_path,
        "model":         "NNETAR" if model_arg == "nnetar" else "EGARCH(1,1)-t",
        "dist":          "student-t",
        "mc_sims":       0 if model_arg == "nnetar" else 500,
    }

# ── ECONOMIC (FRED) ───────────────────────────────────────────────────────────

elif category == "economic":
    obs, resolved_id, series_name = fetch_fred(symbol)

    rows = []
    for o in obs:
        if o["value"] == ".":
            continue
        rows.append({"date": o["date"], "value": float(o["value"])})

    if not rows:
        print(f"ERROR: No valid observations for FRED series '{resolved_id}'", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    out_path = os.path.join(out_dir, f"forecast_{resolved_id}_monthly_{horizon}.csv")
    df.to_csv(out_path, index=False)

    # map horizon to number of periods for ARIMA forecast
    FRED_HORIZON_PERIODS = {
        "1mo": 1, "3mo": 3, "6mo": 6, "1yr": 12,
        # allow intraday aliases gracefully
        "1h": 1, "4h": 3, "1d": 6, "1w": 12,
    }

    meta = {
        "category":      "economic",
        "symbol":        resolved_id,
        "series_name":   series_name,
        "timeframe":     "monthly",
        "horizon":       horizon,
        "horizon_bars":  FRED_HORIZON_PERIODS.get(horizon, 12),
        "n_obs":         len(df),
        "last_value":    float(df["value"].iloc[-1]),
        "last_date":     str(df["date"].iloc[-1].date()),
        "out_path":      out_path,
        "model":         "auto.ARIMA (SARIMA)",
        "mc_sims":       500,
    }

# ── output metadata ───────────────────────────────────────────────────────────

print(json.dumps(meta))
