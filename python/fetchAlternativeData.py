#!/usr/bin/env python3
"""
fetchAlternativeData.py
=======================
Fetches alternative/sentiment data sources for SPY feature engineering.
Sources:
  1. Wikipedia pageviews (Stock_market, S&P_500, Economy)
  2. Google Trends (SPY, crash, recession, buy stocks, volatility)
  3. Congress trading sentiment (Quiver Quantitative free API)
  4. US market holidays + pre-holiday effect (pandas_market_calendars)
  5. MTA NYC subway ridership (NY Open Data)

Output: outputs/markets/cache/alternative_data_daily.csv
Run:    cd ~/discordBot && venv/bin/python3 python/fetchAlternativeData.py
"""
import os, sys, time, math, warnings
import datetime
import requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

BASE_DIR  = os.path.expanduser("~/discordBot")
CACHE_DIR = os.path.join(BASE_DIR, "outputs", "markets", "cache")
OUT_PATH  = os.path.join(CACHE_DIR, "alternative_data_daily.csv")
os.makedirs(CACHE_DIR, exist_ok=True)

START = datetime.date(2015, 1, 1)
END   = datetime.date.today()

def date_range(start, end):
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += datetime.timedelta(days=1)
    return dates

def z21(series):
    return (series - series.rolling(21).mean()) / series.rolling(21).std()


# ── build skeleton ────────────────────────────────────────────────────────────
all_dates = date_range(START, END)
df = pd.DataFrame({"date": all_dates})
df["date"] = pd.to_datetime(df["date"])
df = df.set_index("date")


# ── 1. Wikipedia pageviews ────────────────────────────────────────────────────
print("[1] Wikipedia pageviews...")
WIKI_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/{article}/daily/{start}/{end}"
WIKI_ARTICLES = {
    "wiki_stock_market_views": "Stock_market",
    "wiki_sp500_views":        "S%26P_500",
    "wiki_economy_views":      "Economy_of_the_United_States",
}
for col, article in WIKI_ARTICLES.items():
    series = {}
    year = START.year
    while year <= END.year:
        s = f"{year}0101"
        e = f"{year}1231" if year < END.year else END.strftime("%Y%m%d")
        url = WIKI_BASE.format(article=article, start=s+"00", end=e+"00")
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "SPYBot/1.0"})
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    d = item["timestamp"][:8]
                    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                    series[d] = item["views"]
            time.sleep(0.3)
        except Exception as e:
            print(f"  [warn] wiki {article} {year}: {e}")
        year += 1
    s = pd.Series(series, name=col)
    s.index = pd.to_datetime(s.index)
    df[col] = s.reindex(df.index)
    print(f"  {col}: {df[col].notna().sum()} rows")

if all(c in df.columns for c in WIKI_ARTICLES.keys()):
    df["wiki_total_views"] = df[list(WIKI_ARTICLES.keys())].sum(axis=1)
    df["wiki_views_z21"]   = z21(df["wiki_total_views"])
    print(f"  wiki_total_views: {df['wiki_total_views'].notna().sum()} rows")


# ── 2. Google Trends ──────────────────────────────────────────────────────────
print("[2] Google Trends...")
try:
    from pytrends.request import TrendReq
    pytrends = TrendReq(hl="en-US", tz=300, timeout=(10, 30), retries=2, backoff_factor=1)
    KEYWORDS = ["SPY", "stock market crash", "recession", "buy stocks", "market volatility"]
    COL_MAP  = dict(zip(KEYWORDS, ["trends_spy","trends_crash","trends_recession","trends_buy_stocks","trends_volatility"]))
    trends_data = {}
    # Fetch in 3-month chunks for daily granularity
    chunk_start = START
    while chunk_start <= END:
        chunk_end = min(chunk_start + datetime.timedelta(days=89), END)
        tf = f"{chunk_start.strftime('%Y-%m-%d')} {chunk_end.strftime('%Y-%m-%d')}"
        try:
            pytrends.build_payload(KEYWORDS, timeframe=tf, geo="US")
            df_chunk = pytrends.interest_over_time()
            if df_chunk is not None and not df_chunk.empty:
                df_chunk = df_chunk.drop(columns=["isPartial"], errors="ignore")
                for kw in KEYWORDS:
                    if kw in df_chunk.columns:
                        for dt, val in df_chunk[kw].items():
                            d = dt.strftime("%Y-%m-%d")
                            if kw not in trends_data:
                                trends_data[kw] = {}
                            trends_data[kw][d] = val
            time.sleep(8)  # polite rate limiting
        except Exception as e:
            print(f"  [warn] trends chunk {tf}: {e}")
            time.sleep(15)
        chunk_start = chunk_end + datetime.timedelta(days=1)

    for kw, col in COL_MAP.items():
        if kw in trends_data:
            s = pd.Series(trends_data[kw], name=col)
            s.index = pd.to_datetime(s.index)
            df[col] = s.reindex(df.index).interpolate("time")

    trend_cols = list(COL_MAP.values())
    avail = [c for c in trend_cols if c in df.columns]
    if avail:
        df["trends_fear_index"] = df[["trends_crash","trends_recession","trends_volatility"]].mean(axis=1)
        df["trends_fear_z21"]   = z21(df["trends_fear_index"])
        print(f"  trends fetched: {len(avail)} keywords, {df[avail[0]].notna().sum()} rows")
except Exception as e:
    print(f"  [warn] Google Trends failed: {e}")


# ── 3. Congress trading ───────────────────────────────────────────────────────
print("[3] Congress trading (Quiver Quantitative)...")
try:
    QUIVER_URL = "https://api.quiverquant.com/beta/live/congresstrading"
    r = requests.get(QUIVER_URL, timeout=30, headers={
        "User-Agent": "SPYBot/1.0",
        "accept": "application/json",
    })
    if r.status_code == 200:
        trades = r.json()
        cdf = pd.DataFrame(trades)
        cdf["TransactionDate"] = pd.to_datetime(cdf["TransactionDate"], errors="coerce")
        cdf = cdf.dropna(subset=["TransactionDate"])
        cdf["Amount"] = pd.to_numeric(cdf["Amount"], errors="coerce").fillna(0)
        cdf["is_buy"] = cdf["Transaction"].str.contains("Purchase", case=False, na=False)

        daily = cdf.groupby("TransactionDate").apply(lambda g: pd.Series({
            "congress_net_buy_count": g["is_buy"].sum() - (~g["is_buy"]).sum(),
            "congress_buy_vol_usd":   g.loc[g["is_buy"],  "Amount"].sum(),
            "congress_sell_vol_usd":  g.loc[~g["is_buy"], "Amount"].sum(),
        })).reset_index()
        daily["congress_net_flow"] = daily["congress_buy_vol_usd"] - daily["congress_sell_vol_usd"]
        daily["congress_buy_flag"] = (daily["congress_net_buy_count"] > 0).astype(int)
        daily = daily.set_index("TransactionDate")

        for col in ["congress_net_buy_count","congress_buy_vol_usd","congress_sell_vol_usd","congress_net_flow","congress_buy_flag"]:
            df[col] = daily[col].reindex(df.index).fillna(0)

        print(f"  congress trades: {len(trades)} records, {df['congress_net_flow'].notna().sum()} days")
    else:
        print(f"  [warn] QuiverQuant status {r.status_code}")
except Exception as e:
    print(f"  [warn] Congress trading failed: {e}")


# ── 4. Market holidays + pre-holiday effect ───────────────────────────────────
print("[4] Market holidays (pandas_market_calendars)...")
try:
    import pandas_market_calendars as mcal
    nyse = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=str(START), end_date=str(END + datetime.timedelta(days=365)))
    trading_days = set(sched.index.strftime("%Y-%m-%d"))
    all_business = pd.bdate_range(str(START), str(END + datetime.timedelta(days=365)))
    holiday_days = set(all_business.strftime("%Y-%m-%d")) - trading_days

    pre_holiday, post_holiday, days_to_hol, hol_week = {}, {}, {}, {}
    sorted_holidays = sorted([pd.Timestamp(d) for d in holiday_days])

    for dt in df.index:
        next_td = dt + pd.offsets.BDay(1)
        prev_td = dt - pd.offsets.BDay(1)
        pre_holiday[dt]  = 1 if next_td.strftime("%Y-%m-%d") in holiday_days else 0
        post_holiday[dt] = 1 if prev_td.strftime("%Y-%m-%d") in holiday_days else 0
        future_hols = [h for h in sorted_holidays if h > dt]
        days_to_hol[dt] = min((future_hols[0] - dt).days, 10) if future_hols else 10
        week_start = dt - pd.offsets.Week(weekday=0)
        week_end   = week_start + datetime.timedelta(days=4)
        hol_week[dt] = 1 if any(week_start <= h <= week_end for h in sorted_holidays) else 0

    df["is_pre_holiday"]       = pd.Series(pre_holiday).reindex(df.index).fillna(0)
    df["is_post_holiday"]      = pd.Series(post_holiday).reindex(df.index).fillna(0)
    df["days_to_next_holiday"] = pd.Series(days_to_hol).reindex(df.index).fillna(10)
    df["holiday_week"]         = pd.Series(hol_week).reindex(df.index).fillna(0)
    print(f"  pre_holiday days: {df['is_pre_holiday'].sum():.0f}, holiday_week days: {df['holiday_week'].sum():.0f}")
except Exception as e:
    print(f"  [warn] market holidays failed: {e}")


# ── 5. MTA ridership ──────────────────────────────────────────────────────────
print("[5] MTA NYC ridership...")
try:
    MTA_URL = "https://data.ny.gov/resource/vxuj-8kew.json"
    records = []
    offset  = 0
    while True:
        r = requests.get(MTA_URL, params={"$limit": 2000, "$offset": offset, "$order": "date ASC"}, timeout=30)
        batch = r.json()
        if not batch:
            break
        records.extend(batch)
        if len(batch) < 2000:
            break
        offset += 2000
        time.sleep(0.3)

    mdf = pd.DataFrame(records)
    mdf["date"] = pd.to_datetime(mdf["date"].str[:10])
    mdf["mta_subway_riders"] = pd.to_numeric(mdf["subways_total_estimated_ridership"], errors="coerce")
    mdf["mta_bus_riders"]    = pd.to_numeric(mdf.get("buses_total_estimated_ridersip", pd.Series(dtype=float)), errors="coerce")
    mdf["mta_total_riders"]  = mdf["mta_subway_riders"].fillna(0) + mdf["mta_bus_riders"].fillna(0)
    mdf = mdf.set_index("date")

    for col in ["mta_subway_riders", "mta_bus_riders", "mta_total_riders"]:
        df[col] = mdf[col].reindex(df.index) if col in mdf.columns else np.nan
    df["mta_riders_z21"] = z21(df["mta_subway_riders"])
    print(f"  MTA rows: {df['mta_subway_riders'].notna().sum()}")
except Exception as e:
    print(f"  [warn] MTA ridership failed: {e}")


# ── save ──────────────────────────────────────────────────────────────────────
df = df.reset_index()
df["date"] = df["date"].dt.strftime("%Y-%m-%d")
df = df.sort_values("date").reset_index(drop=True)
df.to_csv(OUT_PATH, index=False)
print(f"\n[fetchAlternativeData] wrote {len(df)} rows -> {OUT_PATH}")
print("  Coverage:")
for col in [c for c in df.columns if c != "date"]:
    pct = df[col].notna().mean() * 100
    if pct > 0:
        print(f"    {col}: {pct:.0f}%")
