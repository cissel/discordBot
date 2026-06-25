"""
spyGaps.py
----------
Reads block_events.csv + block_outcomes.csv and returns a JSON payload
for the /spy gaps Discord command.

Logic:
  - A gap is filled if price has reached the block level at ANY tracked horizon
    (reached_1d / reached_3d / reached_1w / reached_2w / reached_1mo == True).
  - At query time, also fetches today's live intraday bar (high/low) from Alpaca
    to catch same-day fills before the EOD cron runs.
  - Display pool: unfilled gaps from the last 90 days (old perpetual unfills
    are tracked but filtered from the embed).
  - Shows up to 5 most recent from that pool; if pool is empty shows latest filled.
"""

import json
import os
import sys
import time
import warnings
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

load_dotenv(os.path.expanduser("~/discordBot/.env"))
API_KEY    = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}
BASE_URL = "https://data.alpaca.markets/v2"

OUT_DIR      = os.path.expanduser("~/discordBot/outputs/research")
EVENTS_CSV   = os.path.join(OUT_DIR, "block_events.csv")
OUTCOMES_CSV = os.path.join(OUT_DIR, "block_outcomes.csv")

HORIZONS = ["1d", "3d", "1w", "2w", "1mo"]
HORIZON_LABELS = {
    "1d":  "1 Day",
    "3d":  "3 Days",
    "1w":  "1 Week",
    "2w":  "2 Weeks",
    "1mo": "1 Month",
}

HIGH_DEV_THRESHOLD = 0.008

ET = ZoneInfo("America/New_York")

EXCHANGE_MAP = {
    "D": "FINRA ADF (dark pool / OTC)",
    "P": "NYSE Arca",
    "N": "NYSE",
    "Q": "Nasdaq",
    "C": "NYSE National",
    "H": "MIAX",
    "V": "IEX",
    "A": "NYSE American",
    "B": "NASDAQ BX",
    "J": "CBOE EDGA",
    "K": "CBOE EDGX",
    "M": "NYSE Chicago",
    "T": "NASDAQ Int.",
    "Z": "BATS",
    "X": "CBOE",
    "W": "CBOE",
    "E": "CBOE EDGX",
    "L": "Long-Term Stock Exchange",
    "S": "MEMX",
    "U": "Members Exchange (MEMX)",
}


# ── live intraday bar ─────────────────────────────────────────────────────────

def fetch_todays_bar(ticker: str = "SPY") -> dict | None:
    """
    Fetch today's intraday high/low/close from Alpaca (1-day bar for today).
    Returns dict with keys: high, low, close, current_price, or None on failure.
    Uses the snapshot endpoint for the live price, bars for today's H/L.
    """
    try:
        today_str = date.today().isoformat()
        # Today's daily bar (may be incomplete intraday)
        r = requests.get(
            f"{BASE_URL}/stocks/{ticker}/bars",
            headers=ALPACA_HEADERS,
            params={
                "timeframe": "1Day",
                "start":     today_str,
                "end":       today_str,
                "feed":      "sip",
                "adjustment": "all",
            },
            timeout=10,
        )
        r.raise_for_status()
        bars = r.json().get("bars", [])
        if not bars:
            return None
        bar = bars[0]

        # Also fetch latest trade for real-time price
        snap = requests.get(
            f"{BASE_URL}/stocks/{ticker}/trades/latest",
            headers=ALPACA_HEADERS,
            params={"feed": "sip"},
            timeout=10,
        )
        snap.raise_for_status()
        latest_price = snap.json().get("trade", {}).get("p")

        return {
            "high":          float(bar.get("h", 0)),
            "low":           float(bar.get("l", 0)),
            "close":         float(bar.get("c", 0)),
            "current_price": float(latest_price) if latest_price else float(bar.get("c", 0)),
        }
    except Exception:
        return None


# ── gap fill logic ─────────────────────────────────────────────────────────────

def is_gap_filled_by_csv(row: pd.Series) -> bool:
    """Filled if any reached_* column is True in the stored CSV data."""
    for h in HORIZONS:
        if str(row.get(f"reached_{h}", "")).lower() == "true":
            return True
    return False


def is_gap_filled_intraday(row: pd.Series, todays_bar: dict | None) -> bool:
    """
    Check if today's live intraday high/low has crossed the block price,
    catching same-day fills before the EOD cron writes them to the CSV.
    Only applies if the block's trade_date is before today.
    """
    if todays_bar is None:
        return False

    # Only check if the block was on a prior day (today's own blocks are handled normally)
    try:
        block_date = pd.to_datetime(row.get("trade_date")).date()
        if block_date >= date.today():
            return False
    except Exception:
        return False

    block_price = float(row.get("block_price", 0))
    direction   = str(row.get("direction", ""))

    if direction == "above_market":
        # Block above market - filled when price rallies up to it
        return todays_bar["high"] >= block_price
    else:
        # Block below market - filled when price drops down to it
        return todays_bar["low"] <= block_price


def is_gap_filled(row: pd.Series, todays_bar: dict | None) -> bool:
    return is_gap_filled_by_csv(row) or is_gap_filled_intraday(row, todays_bar)


# ── payload builders ───────────────────────────────────────────────────────────

def build_forward_moves(row: pd.Series, market_price: float):
    moves = {}
    for h in HORIZONS:
        close      = row.get(f"close_{h}")
        reached    = str(row.get(f"reached_{h}", "")).lower() == "true"
        pct_toward = row.get(f"pct_toward_{h}", 0.0)

        has_close = (close is not None and
                     not (isinstance(close, float) and np.isnan(close)) and
                     str(close).strip() != "")

        pct_from_market = None
        if has_close and market_price:
            pct_from_market = round((float(close) - market_price) / market_price * 100, 3)

        moves[h] = {
            "label":           HORIZON_LABELS[h],
            "close":           round(float(close), 4) if has_close else None,
            "pct_from_market": pct_from_market,
            "pct_toward_gap":  round(float(pct_toward), 2) if pct_toward is not None else 0.0,
            "reached":         reached,
            "filled":          has_close,
        }
    return moves


def build_block_payload(row: pd.Series, todays_bar: dict | None) -> dict:
    block_price  = float(row["block_price"])
    market_price = float(row["market_price"])
    direction    = str(row.get("direction", ""))
    deviation    = float(row.get("deviation", 0.0))
    dollar_value = float(row.get("dollar_value", 0.0))
    size_shares  = int(row.get("size", 0))
    exchange     = str(row.get("exchange", "?"))
    trade_date   = str(row.get("trade_date", ""))
    block_time   = str(row.get("block_time", ""))

    gap_pct  = round((block_price - market_price) / market_price * 100, 4)
    high_dev = deviation >= HIGH_DEV_THRESHOLD

    forward_moves = build_forward_moves(row, market_price)
    filled_csv    = is_gap_filled_by_csv(row)
    filled_today  = is_gap_filled_intraday(row, todays_bar)
    filled        = filled_csv or filled_today

    # If filled intraday today, annotate the "Today" horizon with live data
    if filled_today and not filled_csv and todays_bar:
        current = todays_bar["current_price"]
        pct_from_mkt = round((current - market_price) / market_price * 100, 3)
        forward_moves["today"] = {
            "label":           "Today (live)",
            "close":           round(current, 4),
            "pct_from_market": pct_from_mkt,
            "pct_toward_gap":  100.0,
            "reached":         True,
            "filled":          True,
        }

    dollar_str = (f"${dollar_value/1_000_000_000:.2f}B"
                  if dollar_value >= 1_000_000_000
                  else f"${dollar_value/1_000_000:.0f}M")

    # Current price vs gap (how far away is it right now)
    distance_pct = None
    if todays_bar:
        current = todays_bar["current_price"]
        if direction == "above_market":
            distance_pct = round((block_price - current) / current * 100, 3)
        else:
            distance_pct = round((current - block_price) / current * 100, 3)

    return {
        "trade_date":    trade_date,
        "block_time":    block_time,
        "block_price":   round(block_price, 4),
        "market_price":  round(market_price, 4),
        "direction":     direction,
        "deviation_pct": round(deviation * 100, 4),
        "gap_pct":       round(gap_pct, 4),
        "dollar_value":  dollar_value,
        "dollar_str":    dollar_str,
        "size_shares":   size_shares,
        "exchange":      exchange,
        "exchange_name": EXCHANGE_MAP.get(exchange, exchange),
        "high_deviation": high_dev,
        "is_filled":     filled,
        "filled_today":  filled_today and not filled_csv,
        "current_price": round(todays_bar["current_price"], 4) if todays_bar else None,
        "distance_pct":  distance_pct,
        "forward_moves": forward_moves,
    }


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(EVENTS_CSV):
        print(json.dumps({"error": "block_events.csv not found"}))
        sys.exit(0)
    if not os.path.exists(OUTCOMES_CSV):
        print(json.dumps({"error": "block_outcomes.csv not found"}))
        sys.exit(0)

    outcomes = pd.read_csv(OUTCOMES_CSV)
    if outcomes.empty:
        print(json.dumps({"error": "No outcome data yet"}))
        sys.exit(0)

    # Fetch today's live bar once
    todays_bar = fetch_todays_bar("SPY")

    df = outcomes.copy()
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.sort_values("trade_date_dt").reset_index(drop=True)

    # Apply fill check (CSV + live intraday)
    df["_filled"] = df.apply(lambda r: is_gap_filled(r, todays_bar), axis=1)

    unfilled = df[~df["_filled"]]
    filled   = df[df["_filled"]]

    # Display pool: unfilled from last 90 days only
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=90)
    recent_unfilled = unfilled[unfilled["trade_date_dt"] >= cutoff]

    if not recent_unfilled.empty:
        to_show   = recent_unfilled.tail(5)
        show_mode = "unfilled"
    else:
        to_show   = filled.tail(1)
        show_mode = "latest_filled"

    blocks = [build_block_payload(row, todays_bar) for _, row in to_show.iterrows()]

    result = {
        "mode":           show_mode,
        "total_events":   len(df),
        "unfilled_count": len(recent_unfilled),
        "unfilled_total": len(unfilled),
        "current_price":  todays_bar["current_price"] if todays_bar else None,
        "blocks":         blocks,
    }

    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
