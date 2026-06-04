#!/usr/bin/env python3
"""
nwsAlerts.py
Fetches active NWS weather alerts for Jacksonville / Duval County, FL.

API: https://api.weather.gov/alerts/active?area=FL  (no key required)

Output: ~/discordBot/outputs/weather/nwsAlerts.csv
  Columns: event, severity, urgency, headline, effective, expires,
           area_desc, description, instruction

If no Duval County alerts exist, writes a single placeholder row.
Prints 'ok' on success.
"""

import sys
import os
import csv

try:
    import requests
except ImportError as e:
    print(f"error: requests library not available: {e}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
NWS_URL = "https://api.weather.gov/alerts/active"
HEADERS = {
    "User-Agent": "discordBot/1.0 personal project",
    "Accept":     "application/geo+json",
}
PARAMS = {"area": "FL"}

OUT_DIR  = os.path.expanduser("~/discordBot/outputs/weather")
OUT_FILE = os.path.join(OUT_DIR, "nwsAlerts.csv")

COL_NAMES = [
    "event", "severity", "urgency", "headline",
    "effective", "expires", "area_desc", "description", "instruction",
]

# Map output columns -> NWS properties keys
PROP_MAP = {
    "event":       "event",
    "severity":    "severity",
    "urgency":     "urgency",
    "headline":    "headline",
    "effective":   "effective",
    "expires":     "expires",
    "area_desc":   "areaDesc",
    "description": "description",
    "instruction": "instruction",
}

PLACEHOLDER_ROW = {
    "event":       "None",
    "severity":    "None",
    "urgency":     "",
    "headline":    "No active alerts for Duval County",
    "effective":   "",
    "expires":     "",
    "area_desc":   "",
    "description": "",
    "instruction": "",
}

# ---------------------------------------------------------------------------
def fetch_alerts():
    """GET NWS alerts for FL; return list of feature dicts."""
    try:
        resp = requests.get(NWS_URL, headers=HEADERS, params=PARAMS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        print("error: NWS API request timed out", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"error: NWS API HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"error: NWS API request failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        data = resp.json()
    except ValueError as e:
        print(f"error: could not parse NWS response JSON: {e}", file=sys.stderr)
        sys.exit(1)

    return data.get("features", [])


def filter_duval(features):
    """Return features where areaDesc contains 'Duval' (case-insensitive)."""
    duval = []
    for feat in features:
        props = feat.get("properties", {})
        area = props.get("areaDesc", "") or ""
        if "duval" in area.lower():
            duval.append(props)
    return duval


def build_row(props):
    """Build a CSV row dict from a properties dict."""
    row = {}
    for col, key in PROP_MAP.items():
        val = props.get(key) or ""
        # Flatten any newlines in description/instruction for CSV readability
        if isinstance(val, str):
            val = val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
        row[col] = val
    return row


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    features  = fetch_alerts()
    duval_props = filter_duval(features)

    if duval_props:
        rows = [build_row(p) for p in duval_props]
    else:
        rows = [PLACEHOLDER_ROW]

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COL_NAMES)
        writer.writeheader()
        writer.writerows(rows)

    print("ok")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
