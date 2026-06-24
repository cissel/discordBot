#!/usr/bin/env python3
"""
fetchWeatherDaily.py
====================
Fetches daily weather from NOAA Climate Data Online (CDO) API for:
  - NYC: Central Park (GHCND:USW00094728) — gold standard station, data from 1869
  - Chicago: O'Hare Airport (GHCND:USW00094846) — most complete recent data

Data types fetched (most reliable from GHCND):
  TMAX  : max temperature (raw tenths of C, /10 -> C)
  TMIN  : min temperature (raw tenths of C, /10 -> C)
  PRCP  : precipitation   (raw tenths of mm, /10 -> mm)
  SNOW  : snowfall        (mm, already converted)

Derived features:
  tavg_{city}         : (tmax + tmin) / 2 in C
  temp_range_{city}   : tmax - tmin (diurnal range — proxy for clear sky)
  prcp_flag_{city}    : 1 if PRCP > 0.5mm (meaningful precipitation)
  cold_flag_{city}    : 1 if tavg < 5C
  hot_flag_{city}     : 1 if tavg > 28C
  snow_flag_{city}    : 1 if SNOW > 0

Methodology note (Hirshleifer & Shumway 2003):
  NYSE returns correlate positively with NYC sunshine (proxy = low PRCP, 
  wide temp_range). The financial literature uses weather as a mood proxy
  for market participants clustered in financial centers.

Setup:
  1. Get free NOAA token at: https://www.ncdc.noaa.gov/cdo-web/token
  2. Add to .env: NOAA_CDO_TOKEN=your_token_here
  3. Run: venv/bin/python3 python/fetchWeatherDaily.py

Output: outputs/markets/cache/weather_daily.csv
Columns: date, tmax_nyc, tmin_nyc, prcp_nyc, snow_nyc, tavg_nyc,
         temp_range_nyc, prcp_flag_nyc, cold_flag_nyc, hot_flag_nyc,
         snow_flag_nyc, [same for chi]
"""

import os
import sys
import time
import datetime
import requests
import pandas as pd
from dotenv import load_dotenv

BASE_DIR  = os.path.expanduser("~/discordBot")
OUT_PATH  = os.path.join(BASE_DIR, "outputs", "markets", "cache", "weather_daily.csv")
ENV_PATH  = os.path.join(BASE_DIR, ".env")
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

load_dotenv(ENV_PATH)
TOKEN = os.getenv("NOAA_CDO_TOKEN", "").strip()
if not TOKEN:
    print("[fetchWeatherDaily] ERROR: NOAA_CDO_TOKEN not found in .env")
    print("  Get a free token at: https://www.ncdc.noaa.gov/cdo-web/token")
    print("  Then add: NOAA_CDO_TOKEN=your_token to ~/discordBot/.env")
    sys.exit(1)

BASE_URL = "https://www.ncdc.noaa.gov/cdo-web/api/v2/data"
HEADERS  = {"token": TOKEN}

STATIONS = {
    "nyc": "GHCND:USW00094728",   # NYC Central Park
    "chi": "GHCND:USW00094846",   # Chicago O'Hare
}
DATATYPES = ["TMAX", "TMIN", "PRCP", "SNOW"]

# Full history start — matches SPY feature range
FETCH_START = datetime.date(2015, 1, 1)
FETCH_END   = datetime.date.today()


def fetch_year(station_id, year, datatypes, retries=3):
    """Fetch one calendar year of data for a station. Returns list of result dicts."""
    start = f"{year}-01-01"
    end   = f"{year}-12-31"
    params = {
        "datasetid":  "GHCND",
        "stationid":  station_id,
        "startdate":  start,
        "enddate":    end,
        "datatypeid": datatypes,   # list — requests encodes as repeated params
        "limit":      1000,
        "units":      "metric",    # tenths of C for temp, tenths of mm for precip
    }
    for attempt in range(retries):
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
            if resp.status_code == 400:
                print(f"  [warn] HTTP 400 for {station_id} {year}: {resp.text[:200]}")
                return []
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            # Handle pagination (shouldn't be needed for 1 year of 4 datatypes, but safe)
            count = data.get("metadata", {}).get("resultset", {}).get("count", len(results))
            if count > 1000:
                # Fetch remaining pages
                offset = 1001
                while offset <= count:
                    params["offset"] = offset
                    r2 = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
                    r2.raise_for_status()
                    results.extend(r2.json().get("results", []))
                    offset += 1000
                    time.sleep(0.25)
            return results
        except requests.exceptions.RequestException as e:
            print(f"  [warn] fetch error {station_id} {year} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return []


def parse_results(results):
    """Parse flat API results into dict keyed by (date, datatype) -> value."""
    parsed = {}
    for r in results:
        date = r["date"][:10]   # "2023-01-01T00:00:00" -> "2023-01-01"
        dt   = r["datatype"]
        val  = r["value"]
        # TMAX/TMIN/PRCP come in tenths; SNOW is already mm
        if dt in ("TMAX", "TMIN", "PRCP"):
            val = val / 10.0
        parsed[(date, dt)] = val
    return parsed


def build_city_df(city_key, station_id, start_year, end_year):
    """Fetch and build a per-city daily DataFrame."""
    all_data = {}
    for year in range(start_year, end_year + 1):
        print(f"  {city_key} {year}...", end=" ", flush=True)
        results = fetch_year(station_id, year, DATATYPES)
        parsed  = parse_results(results)
        all_data.update(parsed)
        time.sleep(0.25)   # stay well under 5 req/s limit
        print(f"{len(results)} records")

    # Build daily rows
    rows = []
    current = datetime.date(start_year, 1, 1)
    end     = datetime.date(end_year, 12, 31)
    while current <= end:
        d    = current.strftime("%Y-%m-%d")
        tmax = all_data.get((d, "TMAX"))
        tmin = all_data.get((d, "TMIN"))
        prcp = all_data.get((d, "PRCP"), 0.0)
        snow = all_data.get((d, "SNOW"), 0.0)
        # Derived
        tavg       = round((tmax + tmin) / 2, 2) if tmax is not None and tmin is not None else None
        temp_range = round(tmax - tmin, 2)        if tmax is not None and tmin is not None else None
        prcp_flag  = 1 if prcp is not None and prcp > 0.5 else 0
        cold_flag  = 1 if tavg is not None and tavg < 5   else 0
        hot_flag   = 1 if tavg is not None and tavg > 28  else 0
        snow_flag  = 1 if snow is not None and snow > 0   else 0
        rows.append({
            "date":                d,
            f"tmax_{city_key}":        tmax,
            f"tmin_{city_key}":        tmin,
            f"prcp_{city_key}":        prcp,
            f"snow_{city_key}":        snow,
            f"tavg_{city_key}":        tavg,
            f"temp_range_{city_key}":  temp_range,
            f"prcp_flag_{city_key}":   prcp_flag,
            f"cold_flag_{city_key}":   cold_flag,
            f"hot_flag_{city_key}":    hot_flag,
            f"snow_flag_{city_key}":   snow_flag,
        })
        current += datetime.timedelta(days=1)
    return pd.DataFrame(rows)


def main():
    print(f"[fetchWeatherDaily] fetching {FETCH_START.year}-{FETCH_END.year}")
    print(f"  Stations: {list(STATIONS.keys())}")

    # Load existing cache to do incremental updates
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH, parse_dates=["date"])
        last_date = existing["date"].max().date()
        start_year = last_date.year   # refetch current year in case of gaps
        print(f"  Existing cache: {len(existing)} rows, last={last_date}. Refetching from {start_year}.")
    else:
        existing = None
        start_year = FETCH_START.year
        print(f"  No cache found. Full fetch from {start_year}.")

    end_year = FETCH_END.year

    # Fetch each city
    city_dfs = []
    for city_key, station_id in STATIONS.items():
        print(f"\n  Fetching {city_key} ({station_id})...")
        df_city = build_city_df(city_key, station_id, start_year, end_year)
        city_dfs.append(df_city)

    # Merge cities on date
    merged = city_dfs[0]
    for df_c in city_dfs[1:]:
        merged = merged.merge(df_c, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)

    # Merge with existing (keep old rows, replace updated years)
    if existing is not None:
        existing["date"] = existing["date"].dt.strftime("%Y-%m-%d")
        cutoff = f"{start_year}-01-01"
        old_rows = existing[existing["date"] < cutoff]
        merged = pd.concat([old_rows, merged], ignore_index=True)
        merged = merged.sort_values("date").drop_duplicates("date").reset_index(drop=True)

    merged.to_csv(OUT_PATH, index=False)
    trading_days = merged.dropna(subset=["tavg_nyc"]).shape[0]
    print(f"\n[fetchWeatherDaily] wrote {len(merged)} rows ({trading_days} with NYC temp data) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
