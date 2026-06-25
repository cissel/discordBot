"""
fetchGoogleTrends.py
Fetches weekly Google Trends for financial keywords, interpolates to daily,
and saves to outputs/markets/cache/google_trends_daily.csv

Terms: 'SPY ETF', 'stock market crash', 'recession', 'buy stocks', 'market volatility',
       'bankruptcy', 'divorce lawyer', 'unemployment', 'casino', 'payday loan',
       'margin call', 'how to invest', 'debt relief'
Uses pytrends with exponential backoff on 429 errors.
Weekly granularity from Google; interpolated to daily with forward-fill.

Composite indices:
  trends_fear_index          = avg z-score of (crash + recession)
  trends_distress_index      = avg z-score of (bankruptcy + unemployment + payday_loan + debt_relief)
  trends_risk_appetite_index = avg z-score of (casino + how_invest + buy_stocks)

Usage: venv/bin/python3 python/fetchGoogleTrends.py
"""
import time, random
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

BASE = Path(__file__).parent.parent
CACHE = BASE / "outputs" / "markets" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = CACHE / "google_trends_daily.csv"

all_dates = pd.date_range("2015-01-01", date.today(), freq="D")
df_out = pd.DataFrame(index=all_dates)
df_out.index.name = "date"

TERMS = {
    "trends_spy":          "SPY ETF",
    "trends_crash":        "stock market crash",
    "trends_recession":    "recession",
    "trends_buy_stocks":   "buy stocks",
    "trends_volatility":   "market volatility",
    "trends_bankruptcy":   "bankruptcy",
    "trends_divorce":      "divorce lawyer",
    "trends_unemployment": "unemployment",
    "trends_casino":       "casino",
    "trends_payday_loan":  "payday loan",
    "trends_margin_call":  "margin call",
    "trends_how_invest":   "how to invest",
    "trends_debt_relief":  "debt relief",
}

try:
    from pytrends.request import TrendReq
except ImportError:
    print("[fetchGoogleTrends] pytrends not installed. Run: pip install pytrends")
    # Write empty file so buildSpyFeatures doesn't crash
    df_out.to_csv(OUT)
    exit(0)

def fetch_with_backoff(pt, kw, timeframe, max_retries=5):
    """Fetch a single keyword with exponential backoff on 429."""
    for attempt in range(max_retries):
        try:
            pt.build_payload([kw], cat=0, timeframe=timeframe, geo="US", gprop="")
            df = pt.interest_over_time()
            if df.empty:
                return None
            s = df[kw].astype(float)
            s.index = pd.to_datetime(s.index).normalize()
            return s
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many Requests" in err or "response" in err.lower():
                wait = (2 ** attempt) * 30 + random.uniform(10, 30)
                print(f"  [429] retry {attempt+1}/{max_retries} for '{kw}', waiting {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"  [warn] '{kw}' failed: {e}")
                return None
    print(f"  [warn] '{kw}' exhausted retries")
    return None

def fetch_trends_chunked(pt, kw, col_name):
    """Fetch in 5-year chunks (Google Trends weekly max ~5 years for full granularity)."""
    chunks = [
        ("2015-01-01 2019-12-31", "2015-01-01", "2019-12-31"),
        ("2020-01-01 2022-12-31", "2020-01-01", "2022-12-31"),
        (f"2023-01-01 {date.today().strftime('%Y-%m-%d')}", "2023-01-01", str(date.today())),
    ]
    series_parts = []
    for timeframe, start, end in chunks:
        s = fetch_with_backoff(pt, kw, timeframe)
        if s is not None:
            series_parts.append(s)
        # always pause between requests to avoid rate limiting
        time.sleep(random.uniform(8, 15))

    if not series_parts:
        return None

    # Combine chunks - normalize each chunk to 0-100 scale then stitch
    combined = pd.concat(series_parts).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined

print("[fetchGoogleTrends] Starting... (this takes ~10-15 min due to rate limits)")
pt = TrendReq(hl="en-US", tz=300, timeout=(10, 30), retries=2, backoff_factor=0.5)

for col_name, term in TERMS.items():
    print(f"  Fetching '{term}'...")
    s = fetch_trends_chunked(pt, term, col_name)
    if s is not None:
        # Interpolate weekly -> daily
        s_daily = s.reindex(all_dates).interpolate("time").ffill().bfill()
        df_out[col_name] = s_daily.values
        print(f"  {col_name}: {s.notna().sum()} weekly pts -> {s_daily.notna().sum()} daily rows")
    else:
        df_out[col_name] = np.nan
        print(f"  {col_name}: NaN (fetch failed)")
    # Pause between terms
    time.sleep(random.uniform(15, 25))

# Composite fear index: avg of crash + recession z-scores
try:
    crash_z = (df_out["trends_crash"] - df_out["trends_crash"].mean()) / (df_out["trends_crash"].std() + 1e-9)
    rec_z   = (df_out["trends_recession"] - df_out["trends_recession"].mean()) / (df_out["trends_recession"].std() + 1e-9)
    df_out["trends_fear_index"] = (crash_z + rec_z) / 2
    df_out["trends_fear_z21"] = (
        df_out["trends_fear_index"] - df_out["trends_fear_index"].rolling(21).mean()
    ) / (df_out["trends_fear_index"].rolling(21).std() + 1e-9)
    print("  trends_fear_index: computed from crash + recession z-scores")
except Exception as e:
    print(f"  [warn] fear index failed: {e}")
    df_out["trends_fear_index"] = np.nan
    df_out["trends_fear_z21"] = np.nan

# Composite distress index: avg z-score of bankruptcy + unemployment + payday_loan + debt_relief
try:
    bk_z   = (df_out["trends_bankruptcy"]   - df_out["trends_bankruptcy"].mean())   / (df_out["trends_bankruptcy"].std()   + 1e-9)
    unemp_z = (df_out["trends_unemployment"] - df_out["trends_unemployment"].mean()) / (df_out["trends_unemployment"].std() + 1e-9)
    pay_z  = (df_out["trends_payday_loan"]  - df_out["trends_payday_loan"].mean())  / (df_out["trends_payday_loan"].std()  + 1e-9)
    debt_z = (df_out["trends_debt_relief"]  - df_out["trends_debt_relief"].mean())  / (df_out["trends_debt_relief"].std()  + 1e-9)
    df_out["trends_distress_index"] = (bk_z + unemp_z + pay_z + debt_z) / 4
    print("  trends_distress_index: computed from bankruptcy + unemployment + payday_loan + debt_relief z-scores")
except Exception as e:
    print(f"  [warn] distress index failed: {e}")
    df_out["trends_distress_index"] = np.nan

# Composite risk appetite index: avg z-score of casino + how_invest + buy_stocks
try:
    casino_z    = (df_out["trends_casino"]      - df_out["trends_casino"].mean())      / (df_out["trends_casino"].std()      + 1e-9)
    invest_z    = (df_out["trends_how_invest"]   - df_out["trends_how_invest"].mean())  / (df_out["trends_how_invest"].std()  + 1e-9)
    buystk_z    = (df_out["trends_buy_stocks"]   - df_out["trends_buy_stocks"].mean())  / (df_out["trends_buy_stocks"].std()  + 1e-9)
    df_out["trends_risk_appetite_index"] = (casino_z + invest_z + buystk_z) / 3
    print("  trends_risk_appetite_index: computed from casino + how_invest + buy_stocks z-scores")
except Exception as e:
    print(f"  [warn] risk appetite index failed: {e}")
    df_out["trends_risk_appetite_index"] = np.nan

df_out.index.name = "date"
df_out.to_csv(OUT)
print(f"\n[fetchGoogleTrends] wrote {len(df_out)} rows -> {OUT}")
print("  Coverage:")
for col in df_out.columns:
    cov = df_out[col].notna().mean()
    print(f"    {col}: {cov:.0%}")
