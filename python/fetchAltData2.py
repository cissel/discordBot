#!/usr/bin/env python3
"""
fetchAltData2.py
================
Fetches additional alternative data sources:
  1. NYC Air Quality (PM2.5) via EPA AQS API
  2. Daylight hours for NYC (pure astronomy, no API)
  3. Flight disruption proxy (from existing weather cache)
  4. S&P500 earnings season density (calendar)
  5. Presidential approval rating (FiveThirtyEight)
  6. CBOE Put/Call Ratio (web scrape / yfinance fallback)

Output: outputs/markets/cache/alt_data2_daily.csv
Run:    cd ~/discordBot && venv/bin/python3 python/fetchAltData2.py
"""
import os, sys, time, math, warnings
import datetime
import requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

BASE_DIR  = os.path.expanduser("~/discordBot")
CACHE_DIR = os.path.join(BASE_DIR, "outputs", "markets", "cache")
OUT_PATH  = os.path.join(CACHE_DIR, "alt_data2_daily.csv")
os.makedirs(CACHE_DIR, exist_ok=True)

START = datetime.date(2015, 1, 1)
END   = datetime.date.today()

def date_range(start, end):
    cur, dates = start, []
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += datetime.timedelta(days=1)
    return dates

def z21(s):
    return (s - s.rolling(21).mean()) / s.rolling(21).std()

all_dates = date_range(START, END)
df = pd.DataFrame({"date": pd.to_datetime(all_dates)}).set_index("date")


# ── 1. Daylight hours NYC (pure math, always works) ──────────────────────────
print("[1] Daylight hours (NYC, pure astronomy)...")
LAT_RAD = math.radians(40.7128)
rows = {}
for d_str in all_dates:
    dt  = datetime.date.fromisoformat(d_str)
    doy = dt.timetuple().tm_yday
    B   = (2 * math.pi / 364) * (doy - 1)
    decl = (0.006918 - 0.399912*math.cos(B) + 0.070257*math.sin(B)
            - 0.006758*math.cos(2*B) + 0.000907*math.sin(2*B))
    cos_ha = -math.tan(LAT_RAD) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha_deg  = math.degrees(math.acos(cos_ha))
    rows[d_str] = 2 * ha_deg / 15   # hours of daylight

dl = pd.Series(rows, name="daylight_hours_nyc")
dl.index = pd.to_datetime(dl.index)
df["daylight_hours_nyc"] = dl.reindex(df.index)
df["daylight_chg_7d"]    = df["daylight_hours_nyc"].diff(7)
df["daylight_z365"]      = (df["daylight_hours_nyc"] -
                             df["daylight_hours_nyc"].rolling(365).mean()) /                              df["daylight_hours_nyc"].rolling(365).std()
print(f"  daylight_hours_nyc: {df['daylight_hours_nyc'].notna().sum()} rows ({df['daylight_hours_nyc'].min():.1f}-{df['daylight_hours_nyc'].max():.1f} hrs)")


# ── 2. EPA AQS - NYC PM2.5 ────────────────────────────────────────────────────
print("[2] EPA AQS NYC PM2.5...")
AQS_URL = "https://aqs.epa.gov/data/api/dailySummaryByCounty/byCounty"
aqi_rows = {}
try:
    for year in range(2016, END.year + 1):
        params = {
            "email":   "test@example.com",
            "key":     "test",
            "param":   "88101",
            "bdate":   f"{year}0101",
            "edate":   f"{year}1231" if year < END.year else END.strftime("%Y%m%d"),
            "state":   "36",
            "county":  "061",
        }
        try:
            r = requests.get(AQS_URL, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json().get("Data", [])
                for item in data:
                    d = item.get("date_local", "")[:10]
                    val = item.get("arithmetic_mean")
                    if d and val is not None:
                        if d not in aqi_rows:
                            aqi_rows[d] = []
                        aqi_rows[d].append(float(val))
                print(f"  EPA {year}: {len(data)} records")
            else:
                print(f"  [warn] EPA {year}: status {r.status_code}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  [warn] EPA {year}: {e}")
except Exception as e:
    print(f"  [warn] EPA AQS outer error: {e}")

if aqi_rows:
    daily_aqi = {d: np.mean(vals) for d, vals in aqi_rows.items()}
    s = pd.Series(daily_aqi, name="aqi_pm25_nyc")
    s.index = pd.to_datetime(s.index)
    df["aqi_pm25_nyc"]  = s.reindex(df.index)
    df["aqi_bad_flag"]  = (df["aqi_pm25_nyc"] > 35).astype(float)
    df["aqi_z21"]       = z21(df["aqi_pm25_nyc"])
    print(f"  aqi_pm25_nyc: {df['aqi_pm25_nyc'].notna().sum()} rows, mean={df['aqi_pm25_nyc'].mean():.1f} ug/m3")
else:
    print("  [warn] EPA AQS returned no data, leaving NaN")
    df["aqi_pm25_nyc"] = np.nan
    df["aqi_bad_flag"] = np.nan
    df["aqi_z21"]      = np.nan


# ── 3. Flight disruption proxy (from weather cache) ──────────────────────────
print("[3] Flight disruption proxy (weather-based)...")
WX_PATH = os.path.join(CACHE_DIR, "weather_daily.csv")
try:
    wx = pd.read_csv(WX_PATH, parse_dates=["date"]).set_index("date")
    prcp_nyc = wx.get("prcp_nyc", pd.Series(dtype=float))
    prcp_chi = wx.get("prcp_chi", pd.Series(dtype=float))
    snow_nyc = wx.get("snow_nyc", pd.Series(dtype=float))
    disruption = (
        (prcp_nyc.reindex(df.index).fillna(0) > 10) |
        (prcp_chi.reindex(df.index).fillna(0) > 10) |
        (snow_nyc.reindex(df.index).fillna(0) > 5)
    ).astype(float)
    df["flight_disruption_flag"] = disruption
    print(f"  flight_disruption_flag: {disruption.sum():.0f} disruption days ({disruption.mean()*100:.1f}%)")
except Exception as e:
    print(f"  [warn] weather proxy failed: {e}")
    df["flight_disruption_flag"] = np.nan


# ── 4. Earnings season density ────────────────────────────────────────────────
print("[4] Earnings season calendar...")
EARNINGS_WINDOWS = [(1, 15, 2, 15), (4, 15, 5, 15), (7, 15, 8, 15), (10, 15, 11, 15)]  # (start_m, start_d, end_m, end_d)
earn_flag, days_to_earn = {}, {}
for d_str in all_dates:
    dt = datetime.date.fromisoformat(d_str)
    in_season = False
    for sm, sd, em, ed in EARNINGS_WINDOWS:
        ws = datetime.date(dt.year, sm, sd)
        we = datetime.date(dt.year, em, ed)
        if ws <= dt <= we:
            in_season = True
            break
    earn_flag[d_str] = 1 if in_season else 0
    # days to next season start
    upcoming = []
    for yr in [dt.year, dt.year + 1]:
        for sm, sd, em, ed in EARNINGS_WINDOWS:
            ws = datetime.date(yr, sm, sd)
            if ws > dt:
                upcoming.append((ws - dt).days)
    days_to_earn[d_str] = min(upcoming) if upcoming else 45
    days_to_earn[d_str] = min(days_to_earn[d_str], 45)

df["earnings_season_flag"]     = pd.Series(earn_flag,   index=pd.to_datetime(list(earn_flag.keys()))).reindex(df.index)
df["days_to_earnings_season"]  = pd.Series(days_to_earn, index=pd.to_datetime(list(days_to_earn.keys()))).reindex(df.index)
print(f"  earnings_season_flag: {df['earnings_season_flag'].sum():.0f} days in season")


# ── 5. Presidential approval rating (FiveThirtyEight) ────────────────────────
print("[5] Presidential approval rating (FiveThirtyEight)...")
APPROVAL_URLS = [
    "https://projects.fivethirtyeight.com/polls/data/approval_topline.csv",
    "https://raw.githubusercontent.com/fivethirtyeight/data/master/presidential-approval-ratings/approval_polllist.csv",
]
approval_loaded = False
for url in APPROVAL_URLS:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "SPYBot/1.0"})
        if r.status_code == 200:
            from io import StringIO
            adf = pd.read_csv(StringIO(r.text))
            print(f"  cols: {list(adf.columns[:8])}")
            # Try topline format
            date_col = next((c for c in ["modeldate","end_date","date","startdate"] if c in adf.columns), None)
            approve_col = next((c for c in ["approve_estimate","pct_estimate","approve"] if c in adf.columns), None)
            subgroup_col = next((c for c in ["subgroup","population","grade"] if c in adf.columns), None)
            if date_col and approve_col:
                if subgroup_col and "Adults" in adf[subgroup_col].values:
                    adf = adf[adf[subgroup_col] == "Adults"]
                adf[date_col] = pd.to_datetime(adf[date_col], errors="coerce")
                adf = adf.dropna(subset=[date_col, approve_col])
                adf = adf.sort_values(date_col)
                # Daily approval via ffill
                daily = adf.groupby(adf[date_col].dt.date)[approve_col].mean()
                daily.index = pd.to_datetime(daily.index)
                full = daily.reindex(df.index).ffill()
                df["pres_approval"]        = full
                df["pres_approval_chg_21d"] = full.diff(21)
                print(f"  pres_approval: {full.notna().sum()} rows, range {full.min():.1f}-{full.max():.1f}")
                approval_loaded = True
                break
    except Exception as e:
        print(f"  [warn] {url}: {e}")

if not approval_loaded:
    print("  [warn] Could not load approval data, leaving NaN")
    df["pres_approval"]         = np.nan
    df["pres_approval_chg_21d"] = np.nan


# ── 6. CBOE Put/Call Ratio ────────────────────────────────────────────────────
print("[6] CBOE Put/Call Ratio...")
pcr_loaded = False
# Try yfinance
try:
    import yfinance as yf
    # CBOE equity PCR via yfinance - try symbol CPCE
    for sym in ["^PCE", "CPCE", "^CPCE"]:
        try:
            hist = yf.download(sym, start="2016-01-01", auto_adjust=True, progress=False)
            if not hist.empty:
                df["cboe_equity_pcr"] = hist["Close"].reindex(df.index)
                df["cboe_pcr_z21"]    = z21(df["cboe_equity_pcr"].ffill())
                df["cboe_pcr_flag"]   = (df["cboe_equity_pcr"] > 1.0).astype(float)
                df["cboe_total_pcr"]  = df["cboe_equity_pcr"]
                df["cboe_index_pcr"]  = np.nan
                print(f"  cboe_equity_pcr via yfinance ({sym}): {df['cboe_equity_pcr'].notna().sum()} rows")
                pcr_loaded = True
                break
        except Exception:
            continue
except Exception as e:
    print(f"  [warn] yfinance PCR: {e}")

if not pcr_loaded:
    # Try scraping CBOE
    try:
        import json as _json
        pcr_data = {}
        test_date = datetime.date(2024, 1, 2)
        end_date  = END
        cur = test_date
        while cur <= end_date:
            url = f"https://www.cboe.com/us/options/market_statistics/daily/?dt={cur.strftime('%Y-%m-%d')}"
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if "__NEXT_DATA__" in r.text:
                start_idx = r.text.find("__NEXT_DATA__") + 16
                end_idx   = r.text.find("</script>", start_idx)
                raw_json  = r.text[start_idx:end_idx]
                data      = _json.loads(raw_json)
                # Navigate JSON to find PCR
                props = data.get("props", {}).get("pageProps", {})
                if "data" in props:
                    for item in props["data"]:
                        if "putCallRatio" in str(item):
                            pcr_data[cur.strftime("%Y-%m-%d")] = item.get("putCallRatio")
                            break
            cur += datetime.timedelta(days=1)
            time.sleep(0.5)
            if len(pcr_data) > 5:  # Got some data, good enough
                break
        if pcr_data:
            s = pd.Series(pcr_data).astype(float)
            s.index = pd.to_datetime(s.index)
            df["cboe_total_pcr"]  = s.reindex(df.index)
            df["cboe_equity_pcr"] = np.nan
            df["cboe_index_pcr"]  = np.nan
            df["cboe_pcr_z21"]    = z21(df["cboe_total_pcr"].ffill())
            df["cboe_pcr_flag"]   = (df["cboe_total_pcr"] > 1.0).astype(float)
            pcr_loaded = True
            print(f"  cboe_total_pcr via scrape: {df['cboe_total_pcr'].notna().sum()} rows")
    except Exception as e:
        print(f"  [warn] CBOE scrape: {e}")

if not pcr_loaded:
    print("  [warn] CBOE PCR: all methods failed, leaving NaN")
    for col in ["cboe_total_pcr","cboe_equity_pcr","cboe_index_pcr","cboe_pcr_z21","cboe_pcr_flag"]:
        df[col] = np.nan


# ── save ──────────────────────────────────────────────────────────────────────
df = df.reset_index()
df["date"] = df["date"].dt.strftime("%Y-%m-%d")
df = df.sort_values("date").reset_index(drop=True)
df.to_csv(OUT_PATH, index=False)
print(f"\n[fetchAltData2] wrote {len(df)} rows -> {OUT_PATH}")
print("  Coverage:")
for col in [c for c in df.columns if c != "date"]:
    pct = df[col].notna().mean() * 100
    if pct > 0:
        print(f"    {col}: {pct:.0f}%")
