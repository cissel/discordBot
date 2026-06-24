#!/usr/bin/env python3
"""
fetchAltData2.py
================
Fetches and computes additional alternative data features for market modeling.

Sources:
  1. NYC Air Quality (PM2.5) — EPA AQS API, fallback OpenAQ
  2. Daylight Hours (NYC) — Pure astronomy formula (Spencer 1971)
  3. Flight Disruption Flag — Synthetic proxy from weather cache (NOAA)
  4. S&P500 Earnings Season Density — Calendar math
  5. Presidential Approval Rating — FiveThirtyEight CSV

Output: /home/jhcv/discordBot/outputs/markets/cache/alt_data2_daily.csv
"""

import os
import sys
import math
import time
import requests
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path("/home/jhcv/discordBot")
CACHE_DIR   = BASE_DIR / "outputs" / "markets" / "cache"
OUTPUT_CSV  = CACHE_DIR / "alt_data2_daily.csv"
WEATHER_CSV = CACHE_DIR / "weather_daily.csv"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── Date range ───────────────────────────────────────────────────────────────
START_DATE = date(2015, 1, 1)
END_DATE   = date.today()

all_dates = pd.date_range(str(START_DATE), str(END_DATE), freq="D")
df = pd.DataFrame({"date": all_dates.strftime("%Y-%m-%d")})
df.set_index("date", inplace=True)


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: NYC Air Quality — EPA AQS API
# ══════════════════════════════════════════════════════════════════════════════
def fetch_epa_aqs_pm25():
    """Fetch daily PM2.5 for Manhattan (state=36, county=061) from EPA AQS.
    Returns dict: {date_str -> arithmetic_mean}"""
    print("\n[AQI] Fetching NYC PM2.5 from EPA AQS API …")
    records = {}
    base_url = "https://aqs.epa.gov/data/api/dailySummaryByCounty/byCounty"
    # AQI PM2.5 data starts around 2015-2016 for this county
    start_year = 2015
    end_year   = END_DATE.year

    for year in range(start_year, end_year + 1):
        bdate = f"{year}0101"
        edate = f"{year}1231"
        url   = (f"{base_url}?email=test@example.com&key=test"
                 f"&param=88101&bdate={bdate}&edate={edate}"
                 f"&state=36&county=061")
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                print(f"  [AQI] EPA HTTP {resp.status_code} for year {year}")
                continue
            data = resp.json()
            # API returns {"Header":..., "Data":[...]}
            if "Data" not in data or not data["Data"]:
                # Sometimes 'Header' has an error message
                hdr = data.get("Header", [{}])
                msg = hdr[0].get("error", "no data") if hdr else "no data"
                print(f"  [AQI] EPA no data for {year}: {msg}")
                continue
            for item in data["Data"]:
                d   = item.get("date_local", "")
                val = item.get("arithmetic_mean", None)
                if d and val is not None:
                    records[d] = float(val)
            print(f"  [AQI] EPA year {year}: {len(data['Data'])} records")
        except Exception as e:
            print(f"  [AQI] EPA error year {year}: {e}")
        time.sleep(0.5)   # be polite

    return records


def fetch_openaq_pm25():
    """Fallback: OpenAQ API v2 for NYC PM2.5 (no auth required).
    Returns dict: {date_str -> mean_value}"""
    print("[AQI] Trying OpenAQ fallback …")
    records = {}
    base_url = "https://api.openaq.org/v2/measurements"
    # Pull month by month to stay under limit=10000
    cur = date(2016, 1, 1)
    end = END_DATE
    headers = {"User-Agent": "discordBot/1.0"}

    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        if nxt > end:
            nxt = end + timedelta(days=1)
        params = {
            "city":       "New York",
            "parameter":  "pm25",
            "date_from":  cur.isoformat(),
            "date_to":    nxt.isoformat(),
            "limit":      10000,
        }
        try:
            resp = requests.get(base_url, params=params,
                                headers=headers, timeout=30)
            if resp.status_code == 200:
                items = resp.json().get("results", [])
                # Aggregate by date
                by_date = {}
                for it in items:
                    dt_str = it.get("date", {}).get("utc", "")[:10]
                    v      = it.get("value", None)
                    if dt_str and v is not None and v >= 0:
                        by_date.setdefault(dt_str, []).append(float(v))
                for dt_s, vals in by_date.items():
                    records[dt_s] = float(np.mean(vals))
        except Exception as e:
            print(f"  [AQI] OpenAQ error {cur}: {e}")
        cur = nxt
        time.sleep(0.3)

    print(f"  [AQI] OpenAQ total records: {len(records)}")
    return records


def build_aqi_columns():
    """Return DataFrame with aqi_pm25_nyc, aqi_bad_flag, aqi_z21."""
    records = fetch_epa_aqs_pm25()

    if len(records) < 100:
        print(f"  [AQI] EPA only got {len(records)} records, trying OpenAQ …")
        oaq = fetch_openaq_pm25()
        # Merge: prefer EPA
        for k, v in oaq.items():
            if k not in records:
                records[k] = v

    out = pd.DataFrame(index=df.index)
    out["aqi_pm25_nyc"] = [records.get(d, np.nan) for d in out.index]

    coverage = out["aqi_pm25_nyc"].notna().sum()
    print(f"  [AQI] Total coverage: {coverage} / {len(out)} days "
          f"({100*coverage/len(out):.1f}%)")

    out["aqi_bad_flag"] = (out["aqi_pm25_nyc"] > 35).astype(float)
    out.loc[out["aqi_pm25_nyc"].isna(), "aqi_bad_flag"] = np.nan

    # 21-day rolling z-score
    roll = out["aqi_pm25_nyc"].rolling(21, min_periods=10)
    out["aqi_z21"] = (out["aqi_pm25_nyc"] - roll.mean()) / roll.std()

    return out


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: Daylight Hours (Spencer 1971 / Duffie & Beckman)
# ══════════════════════════════════════════════════════════════════════════════
def daylight_hours_nyc(d: date) -> float:
    """Compute daylight hours for NYC (lat=40.7128) using Spencer formula."""
    doy = d.timetuple().tm_yday
    B   = (2 * math.pi / 364) * (doy - 1)
    decl = (0.006918
            - 0.399912 * math.cos(B)
            + 0.070257 * math.sin(B)
            - 0.006758 * math.cos(2 * B)
            + 0.000907 * math.sin(2 * B))
    lat_rad = math.radians(40.7128)
    cos_ha  = -math.tan(lat_rad) * math.tan(decl)
    # Clamp to [-1, 1] to handle polar edge cases
    cos_ha  = max(-1.0, min(1.0, cos_ha))
    ha_deg  = math.degrees(math.acos(cos_ha))
    return 2 * ha_deg / 15.0


def build_daylight_columns():
    """Return DataFrame with daylight_hours_nyc, daylight_chg_7d, daylight_z365."""
    print("\n[DAYLIGHT] Computing daylight hours for NYC …")
    out = pd.DataFrame(index=df.index)
    out["daylight_hours_nyc"] = [
        daylight_hours_nyc(datetime.strptime(d, "%Y-%m-%d").date())
        for d in out.index
    ]
    out["daylight_chg_7d"] = out["daylight_hours_nyc"].diff(7)

    # Z-score vs trailing 365 days
    roll365 = out["daylight_hours_nyc"].rolling(365, min_periods=180)
    out["daylight_z365"] = (
        (out["daylight_hours_nyc"] - roll365.mean()) / roll365.std()
    )

    print(f"  [DAYLIGHT] Computed {out['daylight_hours_nyc'].notna().sum()} rows. "
          f"Range: {out['daylight_hours_nyc'].min():.2f}h – "
          f"{out['daylight_hours_nyc'].max():.2f}h")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: Flight Disruption Flag (Synthetic proxy from weather)
# ══════════════════════════════════════════════════════════════════════════════
def build_flight_columns():
    """Synthetic flight disruption proxy from NOAA weather cache.

    NOTE: This is NOT real flight delay data. It is a weather-based proxy:
      flight_disruption_flag = 1 if prcp_nyc > 10mm OR prcp_chi > 10mm OR snow_nyc > 5mm
    Documented proxy per task specification.
    """
    print("\n[FLIGHT] Building synthetic flight disruption proxy from weather cache …")
    out = pd.DataFrame(index=df.index)
    out["flight_disruption_flag"] = np.nan

    if not WEATHER_CSV.exists():
        print(f"  [FLIGHT] Weather cache not found at {WEATHER_CSV}, skipping.")
        return out

    wx = pd.read_csv(WEATHER_CSV, parse_dates=["date"])
    wx["date"] = wx["date"].dt.strftime("%Y-%m-%d")
    wx = wx.set_index("date")

    # Identify relevant columns safely
    has_prcp_nyc = "prcp_nyc" in wx.columns
    has_prcp_chi = "prcp_chi" in wx.columns
    has_snow_nyc = "snow_nyc" in wx.columns

    merged = out.join(wx[
        [c for c in ["prcp_nyc", "prcp_chi", "snow_nyc"] if c in wx.columns]
    ], how="left")

    flag = pd.Series(0, index=out.index)
    if has_prcp_nyc:
        flag = flag | (merged["prcp_nyc"].fillna(0) > 10).astype(int)
    if has_prcp_chi:
        flag = flag | (merged["prcp_chi"].fillna(0) > 10).astype(int)
    if has_snow_nyc:
        flag = flag | (merged["snow_nyc"].fillna(0) > 5).astype(int)

    # Mark as NaN where no weather data at all
    has_wx = pd.Series(False, index=out.index)
    for col in ["prcp_nyc", "prcp_chi", "snow_nyc"]:
        if col in merged.columns:
            has_wx = has_wx | merged[col].notna()

    out["flight_disruption_flag"] = flag.where(has_wx, other=np.nan)

    n_flags = (out["flight_disruption_flag"] == 1).sum()
    n_total = out["flight_disruption_flag"].notna().sum()
    print(f"  [FLIGHT] {n_flags} disruption days / {n_total} days with data "
          f"({100*n_flags/max(n_total,1):.1f}% disruption rate)  [SYNTHETIC PROXY]")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 4: Earnings Season Density (Calendar math)
# ══════════════════════════════════════════════════════════════════════════════
# Earnings season windows (month, start_day, end_day)
EARNINGS_WINDOWS = [
    (1, 15, 2, 15),   # Q4: Jan 15 – Feb 15
    (4, 15, 5, 15),   # Q1: Apr 15 – May 15
    (7, 15, 8, 15),   # Q2: Jul 15 – Aug 15
    (10, 15, 11, 15), # Q3: Oct 15 – Nov 15
]


def earnings_season_info(d: date):
    """Return (in_season: bool, days_to_next: int)."""
    year = d.year

    # Build all season windows for ±2 years to find nearest
    windows = []
    for y in range(year - 1, year + 3):
        for sm, sd, em, ed in EARNINGS_WINDOWS:
            try:
                s = date(y, sm, sd)
                e = date(y, em, ed)
                windows.append((s, e))
            except ValueError:
                pass

    in_season = any(s <= d <= e for s, e in windows)

    # Days to next season start (0 if currently in season)
    if in_season:
        days_to = 0
    else:
        future_starts = [s for s, e in windows if s > d]
        if future_starts:
            days_to = min((s - d).days for s in future_starts)
        else:
            days_to = 45   # shouldn't happen
    days_to = min(days_to, 45)
    return in_season, days_to


def build_earnings_columns():
    """Return DataFrame with earnings_season_flag, days_to_earnings_season."""
    print("\n[EARNINGS] Computing earnings season calendar …")
    out = pd.DataFrame(index=df.index)
    flags, days_to = [], []
    for d_str in out.index:
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        in_s, dt = earnings_season_info(d)
        flags.append(1 if in_s else 0)
        days_to.append(dt)

    out["earnings_season_flag"]    = flags
    out["days_to_earnings_season"] = days_to
    n_season = sum(flags)
    print(f"  [EARNINGS] {n_season} / {len(out)} days in earnings season "
          f"({100*n_season/len(out):.1f}%)")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 5: Presidential Approval Rating (FiveThirtyEight)
# ══════════════════════════════════════════════════════════════════════════════
# President date ranges
PRESIDENT_RANGES = [
    ("Obama",  date(2015, 1, 1),   date(2017, 1, 19)),
    ("Trump",  date(2017, 1, 20),  date(2021, 1, 19)),
    ("Biden",  date(2021, 1, 20),  date(2025, 1, 19)),
    ("Trump",  date(2025, 1, 20),  date(2099, 12, 31)),
]


def president_on_date(d: date) -> str:
    for name, s, e in PRESIDENT_RANGES:
        if s <= d <= e:
            return name
    return "Unknown"


FTE_URLS = [
    # Current live approval tracking
    "https://projects.fivethirtyeight.com/polls/data/approval_topline.csv",
    # GitHub mirror
    "https://raw.githubusercontent.com/fivethirtyeight/data/master/presidential-approval-ratings/approval_polllist.csv",
    # Legacy per-president pages
    "https://projects.fivethirtyeight.com/trump-approval-rating/approval.csv",
    "https://projects.fivethirtyeight.com/biden-approval-rating/approval.csv",
    "https://projects.fivethirtyeight.com/obama-approval-rating/approval.csv",
]


def fetch_fte_approval():
    """Fetch presidential approval from FiveThirtyEight. Returns DataFrame."""
    print("\n[APPROVAL] Fetching presidential approval from FiveThirtyEight …")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; discordBot/1.0; +https://github.com/jhcv)"
        )
    }

    for url in FTE_URLS:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  [APPROVAL] HTTP {resp.status_code} for {url}")
                continue
            from io import StringIO
            raw = pd.read_csv(StringIO(resp.text), low_memory=False)
            print(f"  [APPROVAL] Got {len(raw)} rows from {url}")
            print(f"  [APPROVAL] Columns: {list(raw.columns[:10])}")
            return raw, url
        except Exception as e:
            print(f"  [APPROVAL] Error fetching {url}: {e}")

    return None, None


def parse_fte_approval(raw: pd.DataFrame, source_url: str) -> pd.Series:
    """Parse FTE approval CSV into a date-indexed Series of approve_estimate."""
    cols = [c.lower() for c in raw.columns]
    raw.columns = cols

    # Different CSV formats have different column names
    date_col    = None
    approve_col = None
    subgroup_col = None

    for c in ["modeldate", "end_date", "createddate", "date"]:
        if c in cols:
            date_col = c
            break

    for c in ["approve_estimate", "approve", "pct_estimate"]:
        if c in cols:
            approve_col = c
            break

    for c in ["subgroup", "population", "grade"]:
        if c in cols:
            subgroup_col = c
            break

    if date_col is None or approve_col is None:
        print(f"  [APPROVAL] Cannot identify date/approve columns in {source_url}")
        print(f"  [APPROVAL] Available: {list(cols)}")
        return None

    # Filter to relevant subgroup if possible
    if subgroup_col and subgroup_col in raw.columns:
        unique_sg = raw[subgroup_col].dropna().unique()
        for preferred in ["Adults", "All polls", "All"]:
            if preferred in unique_sg:
                raw = raw[raw[subgroup_col] == preferred]
                print(f"  [APPROVAL] Filtered to subgroup='{preferred}'")
                break

    # Filter to president column if present
    pres_col = None
    for c in ["president", "politician", "subject"]:
        if c in cols:
            pres_col = c
            break

    raw[date_col]    = pd.to_datetime(raw[date_col], errors="coerce")
    raw[approve_col] = pd.to_numeric(raw[approve_col], errors="coerce")
    raw = raw.dropna(subset=[date_col, approve_col])
    raw = raw.sort_values(date_col)

    # If multiple presidents in same file, handle per-president
    # Build a daily series by date
    daily = {}
    if pres_col:
        for _, row in raw.iterrows():
            d_str = row[date_col].strftime("%Y-%m-%d")
            p_on  = president_on_date(row[date_col].date())
            p_raw = str(row.get(pres_col, "")).strip().lower()
            # Only use the approval if it matches who was president on that date
            if p_raw and not any(p.lower() in p_raw for p in [p_on, p_on.lower()]):
                # Skip if president name doesn't match
                # But allow if file only has one president
                pass
            daily[d_str] = float(row[approve_col])
    else:
        for _, row in raw.iterrows():
            d_str = row[date_col].strftime("%Y-%m-%d")
            daily[d_str] = float(row[approve_col])

    s = pd.Series(daily, name="pres_approval")
    s.index = pd.to_datetime(s.index)
    return s


def build_approval_columns():
    """Return DataFrame with pres_approval, pres_approval_chg_21d."""
    out = pd.DataFrame(index=df.index)
    out["pres_approval"]       = np.nan
    out["pres_approval_chg_21d"] = np.nan

    raw, src = fetch_fte_approval()
    if raw is None:
        print("  [APPROVAL] All sources failed, leaving as NaN.")
        return out

    series = parse_fte_approval(raw, src)
    if series is None:
        print("  [APPROVAL] Parse failed, leaving as NaN.")
        return out

    # Re-index to our full date range and forward-fill (weekly polls → daily)
    full_idx = pd.to_datetime(df.index)
    series   = series.reindex(full_idx).ffill().bfill()
    series.index = df.index   # restore string index

    out["pres_approval"] = series
    # 21-day change
    out["pres_approval_chg_21d"] = out["pres_approval"].diff(21)

    cov = out["pres_approval"].notna().sum()
    print(f"  [APPROVAL] Coverage: {cov} / {len(out)} days "
          f"({100*cov/len(out):.1f}%)")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 70)
    print("fetchAltData2.py — Alternative Data Fetcher")
    print(f"Date range: {START_DATE} → {END_DATE}")
    print(f"Output: {OUTPUT_CSV}")
    print("=" * 70)

    # Build each block
    aqi_df      = build_aqi_columns()
    daylight_df = build_daylight_columns()
    flight_df   = build_flight_columns()
    earnings_df = build_earnings_columns()
    approval_df = build_approval_columns()

    # Merge all into master frame
    result = pd.DataFrame(index=df.index)
    result = result.join(aqi_df)
    result = result.join(daylight_df)
    result = result.join(flight_df)
    result = result.join(earnings_df)
    result = result.join(approval_df)

    # Enforce column order
    final_cols = [
        "aqi_pm25_nyc", "aqi_bad_flag", "aqi_z21",
        "daylight_hours_nyc", "daylight_chg_7d", "daylight_z365",
        "flight_disruption_flag",
        "earnings_season_flag", "days_to_earnings_season",
        "pres_approval", "pres_approval_chg_21d",
    ]
    for c in final_cols:
        if c not in result.columns:
            result[c] = np.nan

    result = result[final_cols]
    result.index.name = "date"
    result = result.sort_index()

    # ── Coverage report ──────────────────────────────────────────────────────
    total = len(result)
    print("\n" + "=" * 70)
    print("COVERAGE REPORT")
    print("=" * 70)
    for c in final_cols:
        n    = result[c].notna().sum()
        pct  = 100 * n / total
        flag = "✓" if pct >= 50 else ("~" if pct > 0 else "✗")
        print(f"  {flag} {c:<35s}  {n:>5}/{total}  ({pct:5.1f}%)")

    result.to_csv(OUTPUT_CSV)
    print(f"\n✓ Saved {len(result)} rows × {len(final_cols)} cols → {OUTPUT_CSV}")
    print("Done.")


if __name__ == "__main__":
    main()
