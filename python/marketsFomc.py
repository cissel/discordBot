#!/usr/bin/env python3
"""
marketsFomc.py
Fetches FOMC meeting probabilities and current Fed Funds rate.
Prints a JSON summary to stdout for the Discord bot.

Data sources:
  - Current rate: FRED API (DFEDTARU / DFEDTARL)
  - Implied rates: CME 30-day Fed Funds futures via Yahoo Finance
"""

import sys
import json
import os
import urllib.request
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HARDCODED_FRED_KEY = "d47e2b30bf4826314df23a57408a56a6"
ENV_PATH = os.path.expanduser("~/discordBot/.env")
TIMEOUT = 10

# Upcoming FOMC meeting dates (hardcoded, publicly known)
FOMC_MEETINGS = [
    "2026-07-29",
    "2026-09-16",
    "2026-11-04",
    "2026-12-16",
    "2027-01-27",
    "2027-03-17",
    "2027-04-28",
    "2027-06-09",
]

# Map month letter codes -> FOMC meeting month index (1=Jan)
# ZQ futures month codes: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun,
#                         N=Jul, Q=Aug, U=Sep, V=Oct, X=Nov, Z=Dec
FUTURES_MONTH_MAP = {
    "F": 1,   # Jan
    "G": 2,   # Feb
    "H": 3,   # Mar
    "J": 4,   # Apr
    "K": 5,   # May
    "M": 6,   # Jun
    "N": 7,   # Jul
    "Q": 8,   # Aug
    "U": 9,   # Sep
    "V": 10,  # Oct
    "X": 11,  # Nov
    "Z": 12,  # Dec
}

# Year suffix map: 26 -> 2026, 27 -> 2027
YEAR_CODE_MAP = {
    "25": 2025,
    "26": 2026,
    "27": 2027,
    "28": 2028,
}

# Tickers to fetch, in order
ZQ_TICKERS = [
    "ZQN26.CBT",  # Jul 2026
    "ZQU26.CBT",  # Sep 2026
    "ZQV26.CBT",  # Oct 2026
    "ZQX26.CBT",  # Nov 2026
    "ZQZ26.CBT",  # Dec 2026
    "ZQF27.CBT",  # Jan 2027
    "ZQG27.CBT",  # Feb 2027
    "ZQH27.CBT",  # Mar 2027
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env(path):
    """Read key=value pairs from a .env file, return as dict."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    env[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: could not read .env: {e}", file=sys.stderr)
    return env


def fetch_url(url, headers=None):
    """Fetch a URL and return parsed JSON, or raise."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw)


def get_fred_rate(series_id, api_key):
    """Fetch the latest observation for a FRED series. Returns float or None."""
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}"
        f"&api_key={api_key}"
        f"&file_type=json"
        f"&limit=1"
        f"&sort_order=desc"
    )
    try:
        data = fetch_url(url)
        obs = data.get("observations", [])
        if obs:
            val = obs[0].get("value", ".")
            if val != ".":
                return float(val)
    except Exception as e:
        print(f"FRED fetch error for {series_id}: {e}", file=sys.stderr)
    return None


def parse_ticker(ticker):
    """
    Parse a ZQ ticker like 'ZQN26.CBT' into (month_int, year_int).
    Returns (month, year) or None on failure.
    """
    # Strip exchange suffix
    base = ticker.split(".")[0]  # e.g. ZQN26
    if not base.startswith("ZQ") or len(base) < 5:
        return None
    month_code = base[2]          # e.g. 'N'
    year_code  = base[3:]         # e.g. '26'
    month = FUTURES_MONTH_MAP.get(month_code)
    year  = YEAR_CODE_MAP.get(year_code)
    if month is None or year is None:
        return None
    return month, year


def get_futures_prices():
    """
    Fetch all ZQ futures prices from Yahoo Finance.
    Returns dict: {(month_int, year_int): futures_price}
    """
    prices = {}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    for ticker in ZQ_TICKERS:
        key = parse_ticker(ticker)
        if key is None:
            print(f"Could not parse ticker: {ticker}", file=sys.stderr)
            continue
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval=1d&range=1d"
        )
        try:
            data = fetch_url(url, headers=headers)
            result = data.get("chart", {}).get("result", [])
            if not result:
                print(f"No data for {ticker}", file=sys.stderr)
                continue
            # Try regularMarketPrice first, then close
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is None:
                # Fall back to last close
                closes = (
                    result[0]
                    .get("indicators", {})
                    .get("quote", [{}])[0]
                    .get("close", [])
                )
                if closes:
                    price = closes[-1]
            if price is not None:
                prices[key] = float(price)
                print(f"Fetched {ticker}: {price}", file=sys.stderr)
            else:
                print(f"No price found for {ticker}", file=sys.stderr)
        except Exception as e:
            print(f"Yahoo Finance error for {ticker}: {e}", file=sys.stderr)
    return prices


def find_futures_price(meeting_date, futures_prices, current_rate):
    """
    Find the best futures price for a given meeting date.
    Tries to match the month/year of the meeting date.
    Falls back to the nearest month available on or after the meeting.
    Returns (implied_rate, found_flag).
    """
    m = meeting_date.month
    y = meeting_date.year
    key = (m, y)

    if key in futures_prices:
        return 100.0 - futures_prices[key], True

    # Try the month before (futures settle based on avg rate for that month)
    # so a meeting in month M might be priced in month M-1's contract
    # Try surrounding months as fallback
    for delta in [1, -1, 2, -2]:
        dm = m + delta
        dy = y
        if dm > 12:
            dm -= 12
            dy += 1
        elif dm < 1:
            dm += 12
            dy -= 1
        fkey = (dm, dy)
        if fkey in futures_prices:
            return 100.0 - futures_prices[fkey], True

    return current_rate, False


def compute_probs(implied_rate, current_rate):
    """
    Simplified linear probability model.
    Returns (prob_hold, prob_cut25, prob_hike25) as floats summing ~100.
    """
    diff = implied_rate - current_rate

    # Probability of a 25bp hike
    prob_hike25 = max(0.0, min(100.0, diff / 0.25 * 100.0))
    # Probability of a 25bp cut
    prob_cut25  = max(0.0, min(100.0, -diff / 0.25 * 100.0))
    # Clamp so they don't overlap
    if prob_hike25 + prob_cut25 > 100.0:
        # Shouldn't happen with clamping, but be safe
        total = prob_hike25 + prob_cut25
        prob_hike25 = prob_hike25 / total * 100.0
        prob_cut25  = prob_cut25  / total * 100.0

    prob_hold = max(0.0, 100.0 - prob_cut25 - prob_hike25)
    return round(prob_hold, 1), round(prob_cut25, 1), round(prob_hike25, 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = date.today()

    # --- Load FRED API key ---
    env = load_env(ENV_PATH)
    fred_key = env.get("FRED_API_KEY", HARDCODED_FRED_KEY)

    # --- Fetch current rate ---
    upper = get_fred_rate("DFEDTARU", fred_key)
    lower = get_fred_rate("DFEDTARL", fred_key)

    if upper is None or lower is None:
        print("Warning: could not fetch one or both FRED bounds; using fallback", file=sys.stderr)
        upper = upper or 3.75
        lower = lower or 3.50

    current_rate = round((upper + lower) / 2.0, 4)
    current_range = f"{lower:.2f}-{upper:.2f}"

    print(f"Current rate: {current_rate} ({current_range})", file=sys.stderr)

    # --- Fetch futures prices ---
    futures_prices = get_futures_prices()

    # --- Build meetings list ---
    meetings = []
    for date_str in FOMC_MEETINGS:
        meeting_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        days_away = (meeting_date - today).days
        if days_away <= 0:
            continue

        label = meeting_date.strftime("%b %-d")

        implied_rate, found = find_futures_price(meeting_date, futures_prices, current_rate)
        implied_rate = round(implied_rate, 4)

        if found:
            prob_hold, prob_cut25, prob_hike25 = compute_probs(implied_rate, current_rate)
        else:
            # No data: assume hold
            prob_hold, prob_cut25, prob_hike25 = 100.0, 0.0, 0.0

        meetings.append({
            "date":        date_str,
            "label":       label,
            "days_away":   days_away,
            "prob_hold":   prob_hold,
            "prob_cut25":  prob_cut25,
            "prob_hike25": prob_hike25,
            "implied_rate": implied_rate,
        })

    output = {
        "current_rate":  current_rate,
        "current_range": current_range,
        "meetings":      meetings,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
