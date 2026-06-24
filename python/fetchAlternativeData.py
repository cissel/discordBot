#!/usr/bin/env python3
"""
fetchAlternativeData.py
Fetches alternative data sources and saves combined daily CSV.
Sources: Wikipedia pageviews, CBOE PCR, Google Trends, Congress trading,
         Market holidays, MTA ridership.
"""

import os
import sys
import time
import json
import random
import warnings
import traceback
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd
import requests

# Load .env
from dotenv import load_dotenv
load_dotenv(dotenv_path="/home/jhcv/discordBot/.env")

warnings.filterwarnings("ignore")

# ── Paths ────────────────────────────────────────────────────────────────────
OUTPUT_PATH = "/home/jhcv/discordBot/outputs/markets/cache/alternative_data_daily.csv"
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

# ── Date range ───────────────────────────────────────────────────────────────
START_DATE = date(2015, 1, 1)
TODAY = date.today()

HEADERS = {
    "User-Agent": "AlternativeDataBot/1.0 (educational research; contact@example.com)"
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def make_date_index(start: date, end: date) -> pd.DatetimeIndex:
    return pd.date_range(start=str(start), end=str(end), freq="D")


def z_score_21(series: pd.Series) -> pd.Series:
    """21-day rolling z-score (NaN for warmup)."""
    roll = series.rolling(21, min_periods=21)
    return (series - roll.mean()) / roll.std(ddof=1)


def coverage(df: pd.DataFrame) -> None:
    print("\n=== Coverage Stats ===")
    total = len(df)
    for col in df.columns:
        if col == "date":
            continue
        n = df[col].notna().sum()
        pct = 100 * n / total if total > 0 else 0
        print(f"  {col}: {n}/{total} ({pct:.1f}%)")
    print("=====================\n")


def load_existing_cache() -> pd.DataFrame:
    if os.path.exists(OUTPUT_PATH):
        try:
            df = pd.read_csv(OUTPUT_PATH, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"])
            print(f"[cache] Loaded {len(df)} rows from {OUTPUT_PATH}")
            return df
        except Exception as e:
            print(f"[cache] Could not load existing cache: {e}")
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1 — Wikipedia pageviews
# ─────────────────────────────────────────────────────────────────────────────

WIKI_ARTICLES = {
    "wiki_stock_market_views": "Stock_market",
    "wiki_sp500_views":        "S%26P_500",
    "wiki_economy_views":      "Economy_of_the_United_States",
}

def fetch_wiki_article(article: str, start: date, end: date) -> pd.Series:
    """Fetch daily pageviews for one Wikipedia article in ~1-year chunks."""
    all_rows = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(date(chunk_start.year + 1, chunk_start.month, chunk_start.day) - timedelta(days=1), end)
        s = chunk_start.strftime("%Y%m%d") + "00"
        e = chunk_end.strftime("%Y%m%d") + "00"
        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
            f"/en.wikipedia/all-access/all-agents/{article}/daily/{s}/{e}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("items", []):
                    ts = item["timestamp"][:8]          # YYYYMMDD
                    dt = datetime.strptime(ts, "%Y%m%d").date()
                    all_rows.append({"date": dt, "views": item["views"]})
            elif r.status_code == 404:
                pass  # article has no data in range
            else:
                print(f"    [wiki] {article} {s}-{e}: HTTP {r.status_code}")
        except Exception as exc:
            print(f"    [wiki] {article} {s}-{e}: {exc}")
        time.sleep(0.3)
        chunk_start = chunk_end + timedelta(days=1)

    if not all_rows:
        return pd.Series(dtype=float, name="views")
    df = pd.DataFrame(all_rows).set_index("date")["views"]
    df.index = pd.to_datetime(df.index)
    return df


def fetch_wikipedia(existing: pd.DataFrame) -> pd.DataFrame:
    print("\n[SOURCE 1] Wikipedia pageviews …")

    # Determine missing dates
    all_dates = make_date_index(START_DATE, TODAY)
    if not existing.empty and all(c in existing.columns for c in WIKI_ARTICLES.keys()):
        cached_dates = pd.to_datetime(existing["date"]).values
        covered = existing.loc[existing[list(WIKI_ARTICLES.keys())].notna().all(axis=1), "date"]
        if len(covered) > 0:
            last_covered = pd.to_datetime(covered.max()).date()
            fetch_from = last_covered - timedelta(days=7)   # re-fetch last week to catch updates
        else:
            fetch_from = START_DATE
    else:
        fetch_from = START_DATE

    fetch_from = max(fetch_from, START_DATE)
    print(f"  Fetching from {fetch_from} to {TODAY}")

    result = pd.DataFrame(index=all_dates)
    result.index.name = "date"

    for col, article in WIKI_ARTICLES.items():
        print(f"  Fetching article: {article}")
        series = fetch_wiki_article(article, fetch_from, TODAY)
        result[col] = series
        print(f"    → {series.notna().sum()} days fetched")

    result = result.reset_index()
    result["date"] = pd.to_datetime(result["date"])

    # Merge with existing
    if not existing.empty and any(c in existing.columns for c in WIKI_ARTICLES.keys()):
        wiki_cols = ["date"] + [c for c in WIKI_ARTICLES.keys() if c in existing.columns]
        old_wiki = existing[wiki_cols].copy()
        old_wiki["date"] = pd.to_datetime(old_wiki["date"])
        # Only keep old rows NOT in new fetch range
        cutoff = pd.Timestamp(fetch_from)
        old_wiki = old_wiki[old_wiki["date"] < cutoff]
        new_wiki = result[result["date"] >= cutoff]
        combined = pd.concat([old_wiki, new_wiki], ignore_index=True)
    else:
        combined = result

    combined = combined.sort_values("date").reset_index(drop=True)

    # Reindex to full date range
    full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})
    combined = full.merge(combined, on="date", how="left")

    # Computed columns
    for col in WIKI_ARTICLES.keys():
        if col not in combined.columns:
            combined[col] = np.nan

    combined["wiki_total_views"] = combined[list(WIKI_ARTICLES.keys())].sum(axis=1, min_count=1)
    combined["wiki_views_z21"]   = z_score_21(combined["wiki_total_views"])

    print(f"  wiki_total_views non-null: {combined['wiki_total_views'].notna().sum()}")
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2 — CBOE Put/Call Ratio
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cboe_csv_url() -> pd.DataFrame:
    """Download CBOE's historical PCR CSV directly."""
    urls = [
        "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/totalpc.csv",
        "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/equitypc.csv",
    ]
    dfs = {}
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                from io import StringIO
                content = r.text
                # Skip header lines until we find "DATE"
                lines = content.splitlines()
                header_idx = next((i for i, l in enumerate(lines) if "DATE" in l.upper()), 0)
                csv_text = "\n".join(lines[header_idx:])
                df = pd.read_csv(StringIO(csv_text))
                df.columns = [c.strip().upper() for c in df.columns]
                if "DATE" in df.columns:
                    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
                    df = df.dropna(subset=["DATE"])
                    key = "total" if "totalpc" in url else "equity"
                    dfs[key] = df
                    print(f"    [cboe-csv] {key}: {len(df)} rows")
        except Exception as e:
            print(f"    [cboe-csv] {url}: {e}")
    return dfs


def _parse_cboe_nextdata() -> pd.DataFrame:
    """Try to scrape CBOE page for embedded __NEXT_DATA__."""
    try:
        url = "https://www.cboe.com/us/options/market_statistics/daily/"
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return pd.DataFrame()
        import re
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            return pd.DataFrame()
        data = json.loads(m.group(1))
        # Navigate the JSON tree looking for PCR data
        def find_tables(obj, depth=0):
            if depth > 10:
                return []
            results = []
            if isinstance(obj, dict):
                for v in obj.values():
                    results.extend(find_tables(v, depth + 1))
            elif isinstance(obj, list):
                for item in obj:
                    results.extend(find_tables(item, depth + 1))
            return results
        tables = find_tables(data)
        return pd.DataFrame()
    except Exception as e:
        print(f"    [cboe-next] {e}")
        return pd.DataFrame()


def fetch_cboe_pcr(existing: pd.DataFrame) -> pd.DataFrame:
    print("\n[SOURCE 2] CBOE Put/Call Ratio …")

    # Check what we already have
    pcr_cols = ["cboe_total_pcr", "cboe_equity_pcr", "cboe_index_pcr"]
    full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})

    # Method (a): CBOE CSV files
    pcr_df = pd.DataFrame()
    try:
        dfs = _parse_cboe_csv_url()
        if "total" in dfs and len(dfs["total"]) > 100:
            t = dfs["total"].copy()
            t["date"] = pd.to_datetime(t["DATE"])
            # Find PCR column
            pcr_col = next((c for c in t.columns if "PUT" in c or "CALL" in c or "P/C" in c or "RATIO" in c or "TOTAL" in c.upper() and c != "DATE"), None)
            if pcr_col:
                t = t[["date", pcr_col]].rename(columns={pcr_col: "cboe_total_pcr"})
                t["cboe_total_pcr"] = pd.to_numeric(t["cboe_total_pcr"], errors="coerce")
                pcr_df = t
                print(f"    [cboe] total PCR: {len(pcr_df)} rows via CSV")
    except Exception as e:
        print(f"    [cboe-method-a] {e}")

    # Method (b): try yfinance for ^PCE or equity PCR proxies
    if pcr_df.empty or pcr_df["cboe_total_pcr"].notna().sum() < 100:
        try:
            import yfinance as yf
            # ^PCE = CBOE equity put/call
            ticker = yf.Ticker("^PCE")
            hist = ticker.history(start="2016-01-01", end=str(TODAY + timedelta(days=1)))
            if not hist.empty:
                hist = hist.reset_index()
                hist["date"] = pd.to_datetime(hist["Date"]).dt.normalize()
                hist = hist[["date", "Close"]].rename(columns={"Close": "cboe_equity_pcr"})
                hist["cboe_equity_pcr"] = pd.to_numeric(hist["cboe_equity_pcr"], errors="coerce")
                if pcr_df.empty:
                    pcr_df = hist
                else:
                    pcr_df = pcr_df.merge(hist, on="date", how="outer")
                print(f"    [cboe-yf] equity PCR (^PCE): {hist['cboe_equity_pcr'].notna().sum()} rows")
        except Exception as e:
            print(f"    [cboe-yfinance] {e}")

    # Try ^CPC total put/call
    try:
        import yfinance as yf
        for sym, col in [("^CPC", "cboe_total_pcr"), ("^CPCE", "cboe_equity_pcr"), ("^CPCI", "cboe_index_pcr")]:
            try:
                t2 = yf.Ticker(sym)
                h2 = t2.history(start="2016-01-01", end=str(TODAY + timedelta(days=1)), auto_adjust=False)
                if not h2.empty:
                    h2 = h2.reset_index()
                    h2["date"] = pd.to_datetime(h2["Date"]).dt.normalize()
                    h2 = h2[["date", "Close"]].rename(columns={"Close": col})
                    h2[col] = pd.to_numeric(h2[col], errors="coerce")
                    if pcr_df.empty:
                        pcr_df = h2
                    elif col not in pcr_df.columns:
                        pcr_df = pcr_df.merge(h2, on="date", how="outer")
                    else:
                        # fill NaN in existing
                        pcr_df = pcr_df.merge(h2.rename(columns={col: col + "_new"}), on="date", how="outer")
                        pcr_df[col] = pcr_df[col].fillna(pcr_df[col + "_new"])
                        pcr_df = pcr_df.drop(columns=[col + "_new"])
                    print(f"    [cboe-yf] {sym}: {h2[col].notna().sum()} rows")
            except Exception as ee:
                print(f"    [cboe-yf] {sym}: {ee}")
    except Exception as e:
        print(f"    [cboe-yfinance-batch] {e}")

    # Ensure all PCR columns exist
    if pcr_df.empty:
        print("  [cboe] WARNING: All methods failed, leaving PCR as NaN")
        for col in pcr_cols:
            full[col] = np.nan
    else:
        pcr_df["date"] = pd.to_datetime(pcr_df["date"])
        for col in pcr_cols:
            if col not in pcr_df.columns:
                pcr_df[col] = np.nan
        full = full.merge(pcr_df[["date"] + pcr_cols], on="date", how="left")

    # Merge with existing cache (preserve old data)
    if not existing.empty and "cboe_total_pcr" in existing.columns:
        old = existing[["date"] + [c for c in pcr_cols if c in existing.columns]].copy()
        old["date"] = pd.to_datetime(old["date"])
        # Update: new data takes precedence
        merged = full.merge(old, on="date", how="left", suffixes=("", "_old"))
        for col in pcr_cols:
            if col + "_old" in merged.columns:
                merged[col] = merged[col].fillna(merged[col + "_old"])
                merged = merged.drop(columns=[col + "_old"])
        full = merged

    # Computed columns
    if "cboe_total_pcr" in full.columns:
        full["cboe_pcr_z21"]  = z_score_21(full["cboe_total_pcr"])
        full["cboe_pcr_flag"] = (full["cboe_total_pcr"] > 1.2).astype(float)
        full.loc[full["cboe_total_pcr"].isna(), "cboe_pcr_flag"] = np.nan
    else:
        full["cboe_pcr_z21"]  = np.nan
        full["cboe_pcr_flag"] = np.nan

    print(f"  cboe_total_pcr non-null: {full['cboe_total_pcr'].notna().sum() if 'cboe_total_pcr' in full.columns else 0}")
    return full


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3 — Google Trends
# ─────────────────────────────────────────────────────────────────────────────

TRENDS_KEYWORDS = ["SPY", "stock market crash", "recession", "buy stocks", "market volatility"]
TRENDS_COLS     = ["trends_spy", "trends_crash", "trends_recession", "trends_buy_stocks", "trends_volatility"]
TRENDS_START    = date(2016, 1, 1)


def _rescale_chunk(chunk: pd.DataFrame, overlap_ref: pd.DataFrame = None) -> pd.DataFrame:
    """Normalize chunk 0-100, then rescale to match overlap with previous chunk."""
    for col in chunk.columns:
        mx = chunk[col].max()
        if mx > 0:
            chunk[col] = 100.0 * chunk[col] / mx
        else:
            chunk[col] = chunk[col].astype(float)

    if overlap_ref is not None and len(overlap_ref) > 0:
        overlap_dates = chunk.index.intersection(overlap_ref.index)
        if len(overlap_dates) >= 3:
            for col in chunk.columns:
                if col in overlap_ref.columns:
                    ref_vals = overlap_ref.loc[overlap_dates, col].dropna()
                    new_vals = chunk.loc[ref_vals.index, col].dropna()
                    common = ref_vals.index.intersection(new_vals.index)
                    if len(common) >= 2:
                        ref_mean = ref_vals.loc[common].mean()
                        new_mean = new_vals.loc[common].mean()
                        if new_mean > 0:
                            chunk[col] = chunk[col] * (ref_mean / new_mean)
                            chunk[col] = chunk[col].clip(0, 100)
    return chunk


def fetch_google_trends(existing: pd.DataFrame) -> pd.DataFrame:
    print("\n[SOURCE 3] Google Trends …")

    try:
        from pytrends.request import TrendReq
    except ImportError:
        print("  [trends] pytrends not installed, skipping")
        full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})
        for col in TRENDS_COLS:
            full[col] = np.nan
        full["trends_fear_index"] = np.nan
        full["trends_fear_z21"]   = np.nan
        return full

    # Determine what we already have
    full_index = make_date_index(TRENDS_START, TODAY)
    if not existing.empty and TRENDS_COLS[0] in existing.columns:
        covered = existing.loc[
            existing[TRENDS_COLS].notna().any(axis=1), "date"
        ]
        if len(covered) > 0:
            last_covered = pd.to_datetime(covered.max()).date()
            fetch_from = last_covered - timedelta(days=30)
        else:
            fetch_from = TRENDS_START
    else:
        fetch_from = TRENDS_START

    fetch_from = max(fetch_from, TRENDS_START)
    print(f"  Fetching from {fetch_from} to {TODAY}")

    # Build 3-month chunks
    chunks = []
    chunk_start = fetch_from
    while chunk_start <= TODAY:
        chunk_end = min(
            date(chunk_start.year + (chunk_start.month // 12),
                 ((chunk_start.month - 1 + 3) % 12) + 1,
                 1) - timedelta(days=1),
            TODAY
        )
        if chunk_end < chunk_start:
            chunk_end = TODAY
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    print(f"  {len(chunks)} chunks to fetch")

    all_data = {}  # date -> {col: val}
    prev_chunk_df = None

    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 30), retries=2, backoff_factor=1)

    for i, (cs, ce) in enumerate(chunks):
        tf = f"{cs.strftime('%Y-%m-%d')} {ce.strftime('%Y-%m-%d')}"
        print(f"  Chunk {i+1}/{len(chunks)}: {tf}")
        try:
            pytrends.build_payload(TRENDS_KEYWORDS, cat=0, timeframe=tf, geo="US", gprop="")
            time.sleep(random.uniform(5, 10))
            df_chunk = pytrends.interest_over_time()
            if df_chunk is None or df_chunk.empty:
                print(f"    → empty response")
                continue
            if "isPartial" in df_chunk.columns:
                df_chunk = df_chunk.drop(columns=["isPartial"])
            df_chunk.columns = TRENDS_COLS[:len(df_chunk.columns)]
            df_chunk = df_chunk.astype(float)
            df_chunk = _rescale_chunk(df_chunk.copy(), prev_chunk_df)
            # Store (new dates take precedence but overlap helps stitching)
            for dt, row in df_chunk.iterrows():
                dt_str = pd.Timestamp(dt).date()
                if dt_str not in all_data:
                    all_data[dt_str] = {}
                for col in TRENDS_COLS:
                    if col in row.index and not pd.isna(row[col]):
                        all_data[dt_str][col] = row[col]
            prev_chunk_df = df_chunk
            print(f"    → {len(df_chunk)} rows")
        except Exception as e:
            print(f"    [trends] chunk {tf} failed: {e}")
            time.sleep(15)
            continue

    # Build DataFrame
    full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})
    if all_data:
        trend_df = pd.DataFrame.from_dict(all_data, orient="index")
        trend_df.index = pd.to_datetime(trend_df.index)
        trend_df.index.name = "date"
        trend_df = trend_df.reset_index()
        trend_df["date"] = pd.to_datetime(trend_df["date"])
        for col in TRENDS_COLS:
            if col not in trend_df.columns:
                trend_df[col] = np.nan
        full = full.merge(trend_df[["date"] + TRENDS_COLS], on="date", how="left")
    else:
        for col in TRENDS_COLS:
            full[col] = np.nan

    # Merge with existing cache
    if not existing.empty and TRENDS_COLS[0] in existing.columns:
        old = existing[["date"] + [c for c in TRENDS_COLS if c in existing.columns]].copy()
        old["date"] = pd.to_datetime(old["date"])
        cutoff = pd.Timestamp(fetch_from)
        old = old[old["date"] < cutoff]
        new = full[full["date"] >= cutoff]
        combined = pd.concat([old, new], ignore_index=True)
        combined = combined.sort_values("date").reset_index(drop=True)
        full2 = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})
        full = full2.merge(combined[["date"] + TRENDS_COLS], on="date", how="left")

    for col in TRENDS_COLS:
        if col not in full.columns:
            full[col] = np.nan

    full["trends_fear_index"] = (
        full["trends_crash"] + full["trends_recession"] + full["trends_volatility"]
    ) / 3
    full["trends_fear_z21"] = z_score_21(full["trends_fear_index"])

    print(f"  trends_spy non-null: {full['trends_spy'].notna().sum()}")
    return full


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 4 — Congress trading
# ─────────────────────────────────────────────────────────────────────────────

CONGRESS_COLS = [
    "congress_net_buy_count", "congress_buy_vol_usd",
    "congress_sell_vol_usd", "congress_net_flow", "congress_buy_flag"
]


def _parse_amount(val) -> float:
    """Parse CBOE/Congress amount string like '$1,000 - $15,000' → midpoint."""
    if pd.isna(val) or val == "":
        return 0.0
    val = str(val).replace("$", "").replace(",", "").strip()
    if " - " in val:
        parts = val.split(" - ")
        try:
            return (float(parts[0]) + float(parts[1])) / 2
        except Exception:
            return 0.0
    try:
        return float(val)
    except Exception:
        return 0.0


def fetch_congress_trading(existing: pd.DataFrame) -> pd.DataFrame:
    print("\n[SOURCE 4] Congress trading …")

    all_trades = []

    # Try live endpoint
    for url in [
        "https://api.quiverquant.com/beta/live/congresstrading",
        "https://api.quiverquant.com/beta/historical/congresstrading/SPY",
    ]:
        try:
            r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    all_trades.extend(data)
                    print(f"  [congress] {url}: {len(data)} trades")
                else:
                    print(f"  [congress] {url}: unexpected format {type(data)}")
            else:
                print(f"  [congress] {url}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  [congress] {url}: {e}")
        time.sleep(1)

    full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})

    if not all_trades:
        print("  [congress] No data fetched, leaving as 0")
        for col in CONGRESS_COLS:
            full[col] = 0.0
        return full

    trades_df = pd.DataFrame(all_trades)
    print(f"  [congress] Total records: {len(trades_df)}")
    print(f"  [congress] Columns: {list(trades_df.columns)[:10]}")

    # Normalize column names
    trades_df.columns = [c.strip() for c in trades_df.columns]

    # Find date column
    date_col = next((c for c in trades_df.columns if "transaction" in c.lower() and "date" in c.lower()), None)
    if date_col is None:
        date_col = next((c for c in trades_df.columns if "date" in c.lower()), None)
    if date_col is None:
        print("  [congress] No date column found, leaving as 0")
        for col in CONGRESS_COLS:
            full[col] = 0.0
        return full

    print(f"  [congress] Using date column: {date_col}")
    trades_df["_date"] = pd.to_datetime(trades_df[date_col], errors="coerce")
    trades_df = trades_df.dropna(subset=["_date"])
    trades_df["_date"] = trades_df["_date"].dt.normalize()

    # Find transaction type column
    type_col = next((c for c in trades_df.columns if "type" in c.lower() or "transaction" in c.lower()), None)
    if type_col is None:
        type_col = next((c for c in trades_df.columns if "buy" in c.lower() or "sale" in c.lower() or "trade" in c.lower()), None)

    # Find amount column
    amt_col = next((c for c in trades_df.columns if "amount" in c.lower()), None)

    print(f"  [congress] type_col={type_col}, amt_col={amt_col}")

    if type_col:
        trades_df["_is_buy"] = trades_df[type_col].astype(str).str.lower().str.contains("purchase|buy")
        trades_df["_is_sell"] = trades_df[type_col].astype(str).str.lower().str.contains("sale|sell")
    else:
        trades_df["_is_buy"] = False
        trades_df["_is_sell"] = False

    if amt_col:
        trades_df["_amount"] = trades_df[amt_col].apply(_parse_amount)
    else:
        trades_df["_amount"] = 0.0

    # Aggregate by date
    def agg_day(g):
        buys  = g[g["_is_buy"]]
        sells = g[g["_is_sell"]]
        return pd.Series({
            "congress_net_buy_count": len(buys) - len(sells),
            "congress_buy_vol_usd":   buys["_amount"].sum(),
            "congress_sell_vol_usd":  sells["_amount"].sum(),
        })

    daily = trades_df.groupby("_date").apply(agg_day).reset_index()
    daily = daily.rename(columns={"_date": "date"})
    daily["congress_net_flow"] = daily["congress_buy_vol_usd"] - daily["congress_sell_vol_usd"]
    daily["congress_buy_flag"] = (daily["congress_net_buy_count"] > 0).astype(float)

    daily["date"] = pd.to_datetime(daily["date"])
    full = full.merge(daily[["date"] + CONGRESS_COLS], on="date", how="left")
    for col in CONGRESS_COLS:
        if col not in full.columns:
            full[col] = np.nan
    # Fill missing days with 0 (no activity)
    for col in CONGRESS_COLS:
        full[col] = full[col].fillna(0)

    print(f"  congress days with activity: {(full['congress_net_buy_count'] != 0).sum()}")
    return full


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 5 — Market Holidays
# ─────────────────────────────────────────────────────────────────────────────

HOLIDAY_COLS = ["is_pre_holiday", "days_to_next_holiday", "is_post_holiday", "holiday_week"]


def fetch_market_holidays() -> pd.DataFrame:
    print("\n[SOURCE 5] Market holidays …")
    import pandas_market_calendars as mcal

    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(
        start_date=str(START_DATE),
        end_date="2026-12-31"
    )
    trading_days = schedule.index.normalize()

    # Get holiday dates = calendar days that are NOT trading days in range
    full_cal = pd.date_range(start=str(START_DATE), end="2026-12-31", freq="B")  # business days
    holidays = sorted(set(full_cal) - set(trading_days))
    holidays_set = set(pd.Timestamp(h).date() for h in holidays)

    all_dates = make_date_index(START_DATE, TODAY)
    df = pd.DataFrame({"date": all_dates})

    # Precompute trading days set for fast lookup
    trading_set = set(pd.Timestamp(d).date() for d in trading_days)

    is_pre_holiday   = []
    days_to_next_hol = []
    is_post_holiday  = []
    holiday_week     = []

    for dt in df["date"]:
        d = dt.date()

        # days_to_next_holiday: count trading days from d to next holiday (inclusive of holiday check)
        # is_pre_holiday: last trading day before a holiday
        next_hol = None
        future_hols = sorted(h for h in holidays_set if h > d)
        dtnh = 10  # default cap
        if future_hols:
            next_hol = future_hols[0]
            # Count trading days between d (exclusive) and next_hol (exclusive)
            count = 0
            cursor = d + timedelta(days=1)
            while cursor < next_hol and count < 10:
                if cursor in trading_set:
                    count += 1
                cursor += timedelta(days=1)
            dtnh = min(count, 10)

        # is_pre_holiday: d is a trading day AND next_hol is the very next trading day
        pre = 0
        if d in trading_set and next_hol is not None:
            # Next trading day after d
            nxt = d + timedelta(days=1)
            while nxt not in trading_set and nxt not in holidays_set and (nxt - d).days < 14:
                nxt += timedelta(days=1)
            if nxt in holidays_set:
                pre = 1

        # is_post_holiday: previous calendar day was a holiday
        prev_hols = sorted(h for h in holidays_set if h < d)
        post = 0
        if prev_hols:
            last_hol = prev_hols[-1]
            # Check if d is a trading day and previous trading session was followed by holiday
            prev_td = d - timedelta(days=1)
            while prev_td >= last_hol and prev_td not in trading_set:
                prev_td -= timedelta(days=1)
            # If the gap between prev_td and d contains a holiday
            check = prev_td + timedelta(days=1)
            while check < d:
                if check in holidays_set:
                    post = 1
                    break
                check += timedelta(days=1)

        # holiday_week: any holiday in current Mon-Fri week
        week_start = dt - timedelta(days=dt.weekday())   # Monday
        week_end   = week_start + timedelta(days=4)       # Friday
        hw = int(any(
            week_start.date() <= h <= week_end.date()
            for h in holidays_set
        ))

        is_pre_holiday.append(pre)
        days_to_next_hol.append(dtnh)
        is_post_holiday.append(post)
        holiday_week.append(hw)

    df["is_pre_holiday"]       = is_pre_holiday
    df["days_to_next_holiday"] = days_to_next_hol
    df["is_post_holiday"]      = is_post_holiday
    df["holiday_week"]         = holiday_week

    print(f"  is_pre_holiday days: {sum(is_pre_holiday)}")
    print(f"  is_post_holiday days: {sum(is_post_holiday)}")
    print(f"  holiday_week days: {sum(holiday_week)}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 6 — MTA NYC Subway Ridership
# ─────────────────────────────────────────────────────────────────────────────

MTA_START = date(2020, 3, 1)
MTA_COLS  = ["mta_subway_riders", "mta_bus_riders", "mta_total_riders"]


def fetch_mta_ridership(existing: pd.DataFrame) -> pd.DataFrame:
    print("\n[SOURCE 6] MTA ridership …")

    # Determine fetch range
    if not existing.empty and "mta_subway_riders" in existing.columns:
        covered = existing.loc[
            existing["mta_subway_riders"].notna() & (pd.to_datetime(existing["date"]) >= pd.Timestamp(MTA_START)),
            "date"
        ]
        if len(covered) > 0:
            last_covered = pd.to_datetime(covered.max()).date()
            fetch_from = last_covered - timedelta(days=7)
        else:
            fetch_from = MTA_START
    else:
        fetch_from = MTA_START

    fetch_from = max(fetch_from, MTA_START)
    print(f"  Fetching from {fetch_from} to {TODAY}")

    url = "https://data.ny.gov/resource/vxuj-8kew.json"
    all_rows = []
    offset = 0
    limit = 2000

    while True:
        params = {
            "$limit": limit,
            "$offset": offset,
            "$order": "date ASC",
            "$where": f"date >= '{fetch_from.strftime('%Y-%m-%dT00:00:00')}'"
        }
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                print(f"  [mta] HTTP {r.status_code}")
                break
            data = r.json()
            if not data:
                break
            all_rows.extend(data)
            print(f"  [mta] fetched {len(all_rows)} rows …")
            if len(data) < limit:
                break
            offset += limit
            time.sleep(0.5)
        except Exception as e:
            print(f"  [mta] {e}")
            break

    full = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})

    if not all_rows:
        print("  [mta] No data fetched")
        for col in MTA_COLS:
            full[col] = np.nan
        full["mta_riders_z21"] = np.nan
        return full

    mta_df = pd.DataFrame(all_rows)
    print(f"  [mta] Columns: {list(mta_df.columns)[:10]}")

    # Parse date
    date_col = next((c for c in mta_df.columns if c.lower() == "date"), None)
    if date_col is None:
        print("  [mta] No date column found")
        for col in MTA_COLS:
            full[col] = np.nan
        full["mta_riders_z21"] = np.nan
        return full

    mta_df["date"] = pd.to_datetime(mta_df[date_col], errors="coerce").dt.normalize()
    mta_df = mta_df.dropna(subset=["date"])

    # Find subway and bus columns
    subway_col = next(
        (c for c in mta_df.columns if "subway" in c.lower() and "ridership" in c.lower()),
        next((c for c in mta_df.columns if "subway" in c.lower()), None)
    )
    bus_col = next(
        (c for c in mta_df.columns if "bus" in c.lower() and "ridership" in c.lower()),
        next((c for c in mta_df.columns if "bus" in c.lower()), None)
    )

    print(f"  [mta] subway_col={subway_col}, bus_col={bus_col}")

    rename = {}
    if subway_col:
        rename[subway_col] = "mta_subway_riders"
        mta_df[subway_col] = pd.to_numeric(mta_df[subway_col], errors="coerce")
    if bus_col:
        rename[bus_col] = "mta_bus_riders"
        mta_df[bus_col] = pd.to_numeric(mta_df[bus_col], errors="coerce")

    mta_df = mta_df.rename(columns=rename)
    keep_cols = ["date"] + [c for c in ["mta_subway_riders", "mta_bus_riders"] if c in mta_df.columns]
    mta_df = mta_df[keep_cols].groupby("date").mean().reset_index()

    full = full.merge(mta_df, on="date", how="left")

    for col in ["mta_subway_riders", "mta_bus_riders"]:
        if col not in full.columns:
            full[col] = np.nan

    full["mta_total_riders"] = full["mta_subway_riders"].fillna(0) + full["mta_bus_riders"].fillna(0)
    # Re-NaN total if both are NaN
    both_nan = full["mta_subway_riders"].isna() & full["mta_bus_riders"].isna()
    full.loc[both_nan, "mta_total_riders"] = np.nan
    # Dates before MTA start → NaN
    full.loc[full["date"] < pd.Timestamp(MTA_START), MTA_COLS] = np.nan

    # Merge with existing for dates before fetch_from
    if not existing.empty and "mta_subway_riders" in existing.columns:
        old = existing[["date"] + [c for c in MTA_COLS if c in existing.columns]].copy()
        old["date"] = pd.to_datetime(old["date"])
        cutoff = pd.Timestamp(fetch_from)
        old = old[old["date"] < cutoff]
        new = full[full["date"] >= cutoff]
        combined = pd.concat([old, new], ignore_index=True)
        combined = combined.sort_values("date").reset_index(drop=True)
        full2 = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})
        full = full2.merge(combined[["date"] + MTA_COLS], on="date", how="left")

    for col in MTA_COLS:
        if col not in full.columns:
            full[col] = np.nan

    full["mta_riders_z21"] = z_score_21(full["mta_total_riders"])
    print(f"  mta_total_riders non-null: {full['mta_total_riders'].notna().sum()}")
    return full


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("fetchAlternativeData.py")
    print(f"Date range: {START_DATE} → {TODAY}")
    print("=" * 60)

    # Load existing cache
    existing = load_existing_cache()

    # Base date spine
    all_dates = pd.DataFrame({"date": pd.to_datetime(make_date_index(START_DATE, TODAY))})

    # ── Source 1: Wikipedia ──────────────────────────────────────────────────
    wiki_df = pd.DataFrame()
    try:
        wiki_df = fetch_wikipedia(existing)
    except Exception as e:
        print(f"[SOURCE 1] FAILED: {e}")
        traceback.print_exc()
        wiki_df = all_dates.copy()
        for col in list(WIKI_ARTICLES.keys()) + ["wiki_total_views", "wiki_views_z21"]:
            wiki_df[col] = np.nan

    # ── Source 2: CBOE PCR ───────────────────────────────────────────────────
    cboe_df = pd.DataFrame()
    try:
        cboe_df = fetch_cboe_pcr(existing)
    except Exception as e:
        print(f"[SOURCE 2] FAILED: {e}")
        traceback.print_exc()
        cboe_df = all_dates.copy()
        for col in ["cboe_total_pcr", "cboe_equity_pcr", "cboe_index_pcr", "cboe_pcr_z21", "cboe_pcr_flag"]:
            cboe_df[col] = np.nan

    # ── Source 3: Google Trends ──────────────────────────────────────────────
    trends_df = pd.DataFrame()
    try:
        trends_df = fetch_google_trends(existing)
    except Exception as e:
        print(f"[SOURCE 3] FAILED: {e}")
        traceback.print_exc()
        trends_df = all_dates.copy()
        for col in TRENDS_COLS + ["trends_fear_index", "trends_fear_z21"]:
            trends_df[col] = np.nan

    # ── Source 4: Congress ───────────────────────────────────────────────────
    congress_df = pd.DataFrame()
    try:
        congress_df = fetch_congress_trading(existing)
    except Exception as e:
        print(f"[SOURCE 4] FAILED: {e}")
        traceback.print_exc()
        congress_df = all_dates.copy()
        for col in CONGRESS_COLS:
            congress_df[col] = 0.0

    # ── Source 5: Holidays ───────────────────────────────────────────────────
    holiday_df = pd.DataFrame()
    try:
        holiday_df = fetch_market_holidays()
    except Exception as e:
        print(f"[SOURCE 5] FAILED: {e}")
        traceback.print_exc()
        holiday_df = all_dates.copy()
        for col in HOLIDAY_COLS:
            holiday_df[col] = np.nan

    # ── Source 6: MTA ────────────────────────────────────────────────────────
    mta_df = pd.DataFrame()
    try:
        mta_df = fetch_mta_ridership(existing)
    except Exception as e:
        print(f"[SOURCE 6] FAILED: {e}")
        traceback.print_exc()
        mta_df = all_dates.copy()
        for col in MTA_COLS + ["mta_riders_z21"]:
            mta_df[col] = np.nan

    # ── Merge all ────────────────────────────────────────────────────────────
    print("\n[MERGE] Combining all sources …")

    result = all_dates.copy()
    for src_df in [wiki_df, cboe_df, trends_df, congress_df, holiday_df, mta_df]:
        if src_df.empty:
            continue
        src_df = src_df.copy()
        src_df["date"] = pd.to_datetime(src_df["date"])
        # Avoid duplicate columns
        new_cols = [c for c in src_df.columns if c not in result.columns]
        if new_cols:
            result = result.merge(src_df[["date"] + new_cols], on="date", how="left")

    result = result.sort_values("date").reset_index(drop=True)
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")

    # ── Define canonical column order ────────────────────────────────────────
    canonical_cols = [
        "date",
        # Wiki
        "wiki_stock_market_views", "wiki_sp500_views", "wiki_economy_views",
        "wiki_total_views", "wiki_views_z21",
        # CBOE
        "cboe_total_pcr", "cboe_equity_pcr", "cboe_index_pcr",
        "cboe_pcr_z21", "cboe_pcr_flag",
        # Trends
        "trends_spy", "trends_crash", "trends_recession",
        "trends_buy_stocks", "trends_volatility",
        "trends_fear_index", "trends_fear_z21",
        # Congress
        "congress_net_buy_count", "congress_buy_vol_usd",
        "congress_sell_vol_usd", "congress_net_flow", "congress_buy_flag",
        # Holidays
        "is_pre_holiday", "days_to_next_holiday", "is_post_holiday", "holiday_week",
        # MTA
        "mta_subway_riders", "mta_bus_riders", "mta_total_riders", "mta_riders_z21",
    ]

    for col in canonical_cols:
        if col not in result.columns:
            result[col] = np.nan

    result = result[canonical_cols]

    # ── Save ─────────────────────────────────────────────────────────────────
    result.to_csv(OUTPUT_PATH, index=False)
    print(f"\n[SAVED] {OUTPUT_PATH}  ({len(result)} rows × {len(result.columns)} columns)")

    # ── Coverage stats ───────────────────────────────────────────────────────
    result_tmp = result.copy()
    result_tmp["date"] = pd.to_datetime(result_tmp["date"])
    coverage(result_tmp)


if __name__ == "__main__":
    main()
