#!/usr/bin/env python3
"""
marketFear.py
Fetches the CNN Fear & Greed Index and writes it to a CSV.
Output: ~/discordBot/outputs/markets/feargreed.csv
"""

import sys
import csv
import os
import requests

OUTPUT_PATH = os.path.expanduser("~/discordBot/outputs/markets/feargreed.csv")

PRIMARY_URL   = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
FALLBACK_URL  = "https://fear-and-greed-index.p.rapidapi.com/v1/fgi"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch_primary():
    """Fetch from CNN dataviz endpoint. Returns parsed JSON dict or raises."""
    resp = requests.get(PRIMARY_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_fallback():
    """Attempt fallback RapidAPI endpoint (no key). Returns parsed JSON or raises."""
    resp = requests.get(FALLBACK_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def safe_get(lst, idx, key, default=None):
    """Safely index a list and pull a key from the resulting dict."""
    try:
        return lst[idx].get(key, default)
    except (IndexError, TypeError, AttributeError):
        return default


def parse_cnn_data(data):
    """
    Parse the CNN JSON structure.
    Returns a flat dict ready to write as a CSV row.
    """
    fg      = data.get("fear_and_greed", {})
    hist    = data.get("fear_and_greed_historical", {})

    # The historical block may be nested differently depending on API version
    # Try common shapes: list directly, or dict with a 'data' key
    if isinstance(hist, dict):
        hist_list = hist.get("data", [])
    elif isinstance(hist, list):
        hist_list = hist
    else:
        hist_list = []

    # Current reading
    score     = fg.get("score")
    rating    = fg.get("rating")
    timestamp = fg.get("timestamp")

    # Historical snapshots (negative indices from the end of the list)
    # -1 is today's repeated entry; -2 = previous close; -6 ~ one week; -22 ~ one month
    prev_close_score  = safe_get(hist_list, -2,  "y")
    prev_close_rating = safe_get(hist_list, -2,  "rating")
    one_week_score    = safe_get(hist_list, -6,  "y")
    one_week_rating   = safe_get(hist_list, -6,  "rating")
    one_month_score   = safe_get(hist_list, -22, "y")
    one_month_rating  = safe_get(hist_list, -22, "rating")

    return {
        "score":            score,
        "rating":           rating,
        "timestamp":        timestamp,
        "prev_close_score": prev_close_score,
        "prev_close_rating":prev_close_rating,
        "one_week_score":   one_week_score,
        "one_week_rating":  one_week_rating,
        "one_month_score":  one_month_score,
        "one_month_rating": one_month_rating,
    }


def parse_fallback_data(data):
    """
    Parse the RapidAPI fallback JSON.
    Typical shape: { "fgi": { "value": int, "valueText": str }, ... }
    We fill what we can and leave historical fields empty.
    """
    fgi = data.get("fgi", {})
    now = fgi.get("now", fgi)  # some versions nest under 'now'

    score  = now.get("value")
    rating = now.get("valueText")

    prev_close  = data.get("fgi", {}).get("previousClose", {})
    one_week    = data.get("fgi", {}).get("oneWeek", {})
    one_month   = data.get("fgi", {}).get("oneMonth", {})

    return {
        "score":            score,
        "rating":           rating,
        "timestamp":        None,
        "prev_close_score": prev_close.get("value"),
        "prev_close_rating":prev_close.get("valueText"),
        "one_week_score":   one_week.get("value"),
        "one_week_rating":  one_week.get("valueText"),
        "one_month_score":  one_month.get("value"),
        "one_month_rating": one_month.get("valueText"),
    }


def write_csv(row):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    fieldnames = [
        "score", "rating", "timestamp",
        "prev_close_score", "prev_close_rating",
        "one_week_score",   "one_week_rating",
        "one_month_score",  "one_month_rating",
    ]
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def main():
    data = None
    parser = parse_cnn_data

    # --- Try primary ---
    try:
        data = fetch_primary()
        parser = parse_cnn_data
    except Exception as e_primary:
        # --- Try fallback ---
        try:
            data = fetch_fallback()
            parser = parse_fallback_data
        except Exception as e_fallback:
            print(f"error: primary={e_primary} | fallback={e_fallback}")
            sys.exit(1)

    try:
        row = parser(data)
        write_csv(row)
        print("ok")
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
