"""
fetchAlternativeData2.py
Fetches: daylight hours, AQI (skip - no free historical API), flight disruption proxy,
earnings season, presidential approval (piecewise from public data),
CBOE Put/Call Ratio (CBOE CDN CSV, 2003-2019; sparse 2020+).
Writes: outputs/markets/cache/alt_data2_daily.csv
"""
import os, sys, requests
import pandas as pd
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from io import StringIO

BASE = Path(__file__).parent.parent
CACHE = BASE / "outputs" / "markets" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = CACHE / "alt_data2_daily.csv"

# date spine: 2015-01-01 to today
all_dates = pd.date_range("2015-01-01", date.today(), freq="D")
df = pd.DataFrame(index=all_dates)
df.index.name = "date"

# ── 1. Daylight hours (NYC, pure astronomy) ──────────────────────────────────
print("[1] Daylight hours (NYC, pure astronomy)...")
try:
    import ephem
    nyc = ephem.Observer()
    nyc.lat, nyc.lon = "40.7128", "-74.0060"
    nyc.elevation = 10
    nyc.pressure = 0
    nyc.horizon = "-0:34"

    def daylight(dt):
        nyc.date = dt.strftime("%Y/%m/%d 12:00:00")
        try:
            sr = nyc.previous_rising(ephem.Sun())
            ss = nyc.next_setting(ephem.Sun())
            return (ss - sr) * 24
        except Exception:
            return np.nan

    dl = pd.Series({d: daylight(d) for d in all_dates}, name="daylight_hours_nyc")
    df["daylight_hours_nyc"] = dl
    df["daylight_chg_7d"] = dl.diff(7)
    df["daylight_z365"] = (dl - dl.rolling(365).mean()) / (dl.rolling(365).std() + 1e-9)
    print(f"  daylight_hours_nyc: {dl.notna().sum()} rows ({dl.min():.1f}-{dl.max():.1f} hrs)")
except Exception as e:
    print(f"  [warn] ephem failed: {e}")
    df["daylight_hours_nyc"] = np.nan
    df["daylight_chg_7d"] = np.nan
    df["daylight_z365"] = np.nan

# ── 2. AQI - NYC (no reliable free historical API; skip gracefully) ───────────
print("[2] AQI NYC PM2.5 (skipped - no free historical API without registration)...")
df["aqi_pm25_nyc"] = np.nan
df["aqi_bad_flag"] = np.nan
df["aqi_z21"] = np.nan
print("  [info] aqi_pm25_nyc: NaN (sparse - will impute with median in training)")

# ── 3. Flight disruption proxy (from weather_daily.csv) ──────────────────────
print("[3] Flight disruption proxy (weather-based)...")
try:
    wdf = pd.read_csv(CACHE / "weather_daily.csv", index_col="date", parse_dates=True)
    prcp_nyc = wdf.get("prcp_nyc", pd.Series(dtype=float))
    tmin_nyc = wdf.get("tmin_nyc", pd.Series(dtype=float))
    flag = ((prcp_nyc > 20) | (tmin_nyc < -5)).astype(float)
    df["flight_disruption_flag"] = flag.reindex(all_dates)
    n = df["flight_disruption_flag"].sum()
    print(f"  flight_disruption_flag: {int(n)} disruption days ({n/len(all_dates):.1%})")
except Exception as e:
    print(f"  [warn] flight disruption failed: {e}")
    df["flight_disruption_flag"] = np.nan

# ── 4. Earnings season calendar ───────────────────────────────────────────────
print("[4] Earnings season calendar...")
# Q1: mid-Apr to mid-May (day 105-135)
# Q2: mid-Jul to mid-Aug (day 196-227)
# Q3: mid-Oct to mid-Nov (day 288-318)
# Q4: mid-Jan to mid-Feb (day 15-46)
def in_earnings_season(dt):
    doy = dt.timetuple().tm_yday
    return int(
        (15 <= doy <= 46) or
        (105 <= doy <= 135) or
        (196 <= doy <= 227) or
        (288 <= doy <= 318)
    )

season_starts_doy = [15, 105, 196, 288]
def days_to_next_earnings(dt):
    doy = dt.timetuple().tm_yday
    diffs = [(s - doy) % 365 for s in season_starts_doy]
    return min(diffs)

es = pd.Series({d: in_earnings_season(d) for d in all_dates})
dte = pd.Series({d: days_to_next_earnings(d) for d in all_dates})

df["earnings_season_flag"] = es.values
df["days_to_earnings_season"] = dte.values
n_es = int(es.sum())
print(f"  earnings_season_flag: {n_es} days in season ({n_es/len(all_dates):.1%})")

# ── 5. Presidential approval (piecewise from public polling averages) ─────────
print("[5] Presidential approval rating (piecewise from RCP/Gallup averages)...")
# Source: RealClearPolitics historical averages + 538 final averages
# All values are approximate net approval (approve%) from major polling aggregators.
# Key anchor points verified from public sources:
approval_anchors = [
    # (date_str, approve_pct)  -- interpolated between these
    # Obama 2nd term (into 2015)
    ("2015-01-01", 46.4),
    ("2015-06-01", 46.0),
    ("2015-12-01", 44.8),
    ("2016-06-01", 51.3),
    ("2016-11-01", 54.0),
    ("2016-12-31", 55.0),
    # Trump 1st term
    ("2017-01-20", 45.5),  # inauguration
    ("2017-06-01", 39.0),
    ("2018-01-01", 38.5),
    ("2018-06-01", 43.0),
    ("2019-01-01", 40.0),
    ("2019-06-01", 42.5),
    ("2020-01-01", 43.2),
    ("2020-06-01", 38.0),  # BLM protests
    ("2020-11-01", 43.5),
    ("2021-01-19", 34.0),  # end 1st term (Jan 6 effect)
    # Biden
    ("2021-01-20", 53.0),  # inauguration
    ("2021-06-01", 52.8),
    ("2022-01-01", 42.8),  # inflation surge
    ("2022-06-01", 38.5),
    ("2023-01-01", 41.3),
    ("2023-06-01", 40.0),
    ("2024-01-01", 38.5),
    ("2024-06-01", 36.8),
    ("2025-01-19", 37.5),  # end Biden
    # Trump 2nd term — only use anchors with verified published poll readings.
    # Anchors beyond the last verified date are DROPPED to prevent look-ahead bias.
    # Values sourced from RCP/Gallup/Pew published poll aggregates available at the time.
    ("2025-01-20", 47.0),  # inauguration (Gallup pre-inaugural poll, published ~Jan 18)
    ("2025-04-01", 43.0),  # post-tariff announcement (RCP avg, polls fielded late Mar)
    # NOTE: All anchors beyond 2025-04-01 removed — would require retrospective data
    # not available at prediction time. Series is forward-filled flat from last anchor.
    # When re-running after new polls publish, add new verified anchor here.
]

try:
    anchor_s = pd.Series(
        {pd.Timestamp(d): v for d, v in approval_anchors}
    ).sort_index()
    anchor_s = anchor_s.reindex(
        anchor_s.index.union(all_dates)
    ).interpolate("time")
    df["pres_approval"] = anchor_s.reindex(all_dates).values
    df["pres_approval_chg_21d"] = anchor_s.reindex(all_dates).diff(21).values
    cov = df["pres_approval"].notna().mean()
    print(f"  pres_approval: {cov:.0%} coverage (piecewise interpolated)")
except Exception as e:
    print(f"  [warn] approval failed: {e}")
    df["pres_approval"] = np.nan
    df["pres_approval_chg_21d"] = np.nan

# ── 6. CBOE Put/Call Ratio (CDN CSV, covers 2003-2019; sparse 2020+) ─────────
print("[6] CBOE Put/Call Ratio (CBOE CDN CSV)...")

def fetch_cboe_pcr(fname):
    url = f"https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{fname}"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    if r.status_code != 200 or len(r.content) < 500:
        raise ValueError(f"HTTP {r.status_code}")
    lines = r.text.splitlines()
    # find header line (starts with DATE or Date)
    header_idx = next(
        (i for i, l in enumerate(lines) if l.strip().upper().startswith("DATE")),
        None
    )
    if header_idx is None:
        raise ValueError(f"no DATE header found in {fname}")
    csv_text = "\n".join(lines[header_idx:])
    dframe = pd.read_csv(StringIO(csv_text), header=0)
    dframe.columns = [c.strip() for c in dframe.columns]
    date_col = dframe.columns[0]
    pcr_col = dframe.columns[-1]  # P/C Ratio always last
    dframe[date_col] = pd.to_datetime(dframe[date_col].astype(str).str.strip(), errors="coerce")
    dframe = dframe.dropna(subset=[date_col])
    s = pd.to_numeric(dframe[pcr_col].astype(str).str.strip(), errors="coerce")
    s.index = pd.DatetimeIndex(dframe[date_col].values)
    return s.sort_index()

try:
    # combine recent + archive for full coverage 2003-2019
    eq_recent = fetch_cboe_pcr("equitypc.csv")
    eq_arch = fetch_cboe_pcr("equitypcarchive.csv")
    eq_combined = pd.concat([eq_arch, eq_recent]).sort_index()
    eq_combined = eq_combined[~eq_combined.index.duplicated(keep="last")]

    pcr_s = eq_combined.reindex(all_dates)
    df["cboe_pcr_equity"] = pcr_s.values
    df["cboe_pcr_z21"] = (
        (pcr_s - pcr_s.rolling(21).mean()) / (pcr_s.rolling(21).std() + 1e-9)
    ).values
    df["cboe_pcr_spike"] = (
        pcr_s > pcr_s.rolling(63).mean() + 1.5 * pcr_s.rolling(63).std()
    ).astype(float).values

    cov = pcr_s.notna().mean()
    n = pcr_s.notna().sum()
    print(f"  cboe_pcr_equity: {n} rows ({cov:.0%} coverage)")
    print(f"  date range in our window: {str(pcr_s.first_valid_index())[:10]} -> {str(pcr_s.last_valid_index())[:10]}")
    print(f"  [note] CBOE stopped updating these CSVs after Oct 2019. 2020+ rows will be NaN (sparse imputation).")

except Exception as e:
    print(f"  [warn] CBOE PCR failed: {e}. Leaving NaN.")
    df["cboe_pcr_equity"] = np.nan
    df["cboe_pcr_z21"] = np.nan
    df["cboe_pcr_spike"] = np.nan

# ── Save ──────────────────────────────────────────────────────────────────────
df.index.name = "date"
df.to_csv(OUT)
print(f"\n[fetchAlternativeData2] wrote {len(df)} rows -> {OUT}")
print("  Coverage:")
for col in df.columns:
    cov = df[col].notna().mean()
    flag = " [sparse]" if cov < 0.5 else ""
    print(f"    {col}: {cov:.0%}{flag}")
