#!/usr/bin/env python3
"""
fetchRedditWSB.py
Fetch daily WallStreetBets post volume + sentiment from Arctic Shift API.
Arctic Shift = pushshift replacement, full historical Reddit archive.

Output: outputs/features/markets/wsb_daily.csv
Columns: date, wsb_post_count, wsb_avg_score, wsb_avg_comments,
         wsb_posts_z21, wsb_score_z21
Coverage target: 2018-01-01 to today
"""

import os
import sys
import time
import json
import datetime
import requests
import numpy as np
import pandas as pd

# ── config ────────────────────────────────────────────────────────────────────
SUBREDDIT   = "wallstreetbets"
START_DATE  = datetime.date(2018, 1, 1)
BASE_URL    = "https://arctic-shift.photon-reddit.com/api/posts/search"
OUT_PATH    = "outputs/features/markets/wsb_daily.csv"
SLEEP_SEC   = 1.5      # between requests
MAX_PER_REQ = 100      # arctic shift max limit
FIELDS      = "created_utc,score,num_comments"

def fetch_day(date: datetime.date, session: requests.Session) -> dict:
    """Fetch all posts for a single UTC day, return aggregated stats."""
    dt_start = datetime.datetime(date.year, date.month, date.day, 0, 0, 0)
    dt_end   = dt_start + datetime.timedelta(days=1)

    after  = dt_start.strftime("%Y-%m-%dT%H:%M:%S")
    before = dt_end.strftime("%Y-%m-%dT%H:%M:%S")

    scores   = []
    comments = []
    offset   = 0

    while True:
        params = {
            "subreddit": SUBREDDIT,
            "after":     after,
            "before":    before,
            "limit":     MAX_PER_REQ,
            "fields":    FIELDS,
        }
        if offset:
            params["offset"] = offset

        # Arctic Shift 400s on offset pagination for very recent unindexed dates
        recent_cutoff = (datetime.date.today() - datetime.timedelta(days=7))
        if date >= recent_cutoff and offset > 0:
            break

        try:
            resp = session.get(BASE_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", []) or []
        except Exception as e:
            print(f"  [WARN] {date} offset={offset}: {e}")
            break

        if not data:
            break

        for post in data:
            scores.append(post.get("score", 0) or 0)
            comments.append(post.get("num_comments", 0) or 0)

        if len(data) < MAX_PER_REQ:
            break  # last page

        offset += MAX_PER_REQ
        time.sleep(0.3)  # brief pause between pages of same day

    n = len(scores)
    return {
        "date":             str(date),
        "wsb_post_count":   n,
        "wsb_avg_score":    round(float(np.mean(scores)),   2) if n else 0.0,
        "wsb_avg_comments": round(float(np.mean(comments)), 2) if n else 0.0,
    }

def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    today = datetime.date.today()

    # load existing to skip already-fetched dates
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH, parse_dates=["date"])
        existing["date"] = existing["date"].dt.date
        fetched_dates = set(existing["date"].tolist())
        print(f"[fetchRedditWSB] loaded {len(existing)} existing rows")
    else:
        existing = pd.DataFrame()
        fetched_dates = set()

    # build list of missing market days (Mon-Fri)
    all_dates = [
        START_DATE + datetime.timedelta(days=i)
        for i in range((today - START_DATE).days + 1)
    ]
    missing = [d for d in all_dates if d not in fetched_dates and d.weekday() < 5]
    print(f"[fetchRedditWSB] fetching {len(missing)} missing dates ({START_DATE} to {today})")

    if not missing:
        print("[fetchRedditWSB] nothing to fetch - all dates present")
        _add_zscores(OUT_PATH)
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "spy-model-fetcher/1.0"})

    rows = []
    total = len(missing)
    for i, d in enumerate(missing):
        row = fetch_day(d, session)
        rows.append(row)

        if (i + 1) % 50 == 0 or i == total - 1:
            pct = (i + 1) / total * 100
            print(f"  [{i+1}/{total}] {d}  posts={row['wsb_post_count']}  ({pct:.1f}%)")

        time.sleep(SLEEP_SEC)

    # combine + save
    new_df = pd.DataFrame(rows)
    if not existing.empty:
        combined = pd.concat([existing.drop(columns=["wsb_posts_z21","wsb_score_z21"], errors="ignore"),
                              new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    combined = _add_zscores_df(combined)
    combined.to_csv(OUT_PATH, index=False)

    n_rows   = len(combined)
    coverage = n_rows / total * 100 if total else 0
    print(f"[fetchRedditWSB] done. {n_rows} rows | {START_DATE} to {today} | coverage ~{coverage:.1f}%")
    print(f"[fetchRedditWSB] saved -> {OUT_PATH}")

def _add_zscores_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add 21-day z-scores for post count and score."""
    df = df.copy()
    for col, zcol in [("wsb_post_count", "wsb_posts_z21"), ("wsb_avg_score", "wsb_score_z21")]:
        if col in df.columns:
            roll_mean = df[col].rolling(21, min_periods=5).mean()
            roll_std  = df[col].rolling(21, min_periods=5).std()
            df[zcol]  = ((df[col] - roll_mean) / roll_std.replace(0, np.nan)).round(4)
    return df

def _add_zscores(path: str):
    df = pd.read_csv(path)
    df = _add_zscores_df(df)
    df.to_csv(path, index=False)

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
