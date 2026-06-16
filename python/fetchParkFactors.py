#!/usr/bin/env python3
"""
fetchParkFactors.py
===================
Scrapes Fangraphs park factors for 2024, 2025, 2026 and saves as a CSV lookup.

Output: outputs/sports/mlb/fantasy/playerData/park_factors.csv
Columns: season, team, pf_basic (5yr), pf_1yr, pf_hr, pf_1b, pf_2b, pf_3b

Park factor interpretation: 100 = neutral, >100 = hitter-friendly, <100 = pitcher-friendly.
Run this once (or seasonally) - park factors don't change much during a season.
"""

import os, re, time
import urllib.request
import pandas as pd

OUT = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/playerData/park_factors.csv")

# Fangraphs team name -> ESPN/baseballr team abbreviation mapping
TEAM_MAP = {
    "Angels":        "LAA", "Astros":      "HOU", "Athletics":   "OAK",
    "Blue Jays":     "TOR", "Braves":      "ATL", "Brewers":     "MIL",
    "Cardinals":     "STL", "Cubs":        "CHC", "Diamondbacks":"ARI",
    "Dodgers":       "LAD", "Giants":      "SFG", "Guardians":   "CLE",
    "Mariners":      "SEA", "Marlins":     "MIA", "Mets":        "NYM",
    "Nationals":     "WSN", "Orioles":     "BAL", "Padres":      "SDP",
    "Phillies":      "PHI", "Pirates":     "PIT", "Rangers":     "TEX",
    "Rays":          "TBR", "Red Sox":     "BOS", "Reds":        "CIN",
    "Rockies":       "COL", "Royals":      "KCR", "Tigers":      "DET",
    "Twins":         "MIN", "White Sox":   "CHW", "Yankees":     "NYY",
}

def fetch_park_factors_for_year(season: int) -> pd.DataFrame:
    url = f"https://www.fangraphs.com/guts.aspx?type=pf&teamid=0&season={season}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [warn] Failed to fetch {season}: {e}")
        return pd.DataFrame()

    # Parse the park factors table from HTML
    # Find all <tr> rows inside the table
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    records = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) >= 6:
            # Strip HTML tags from each cell
            clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if clean[0].isdigit() and int(clean[0]) == season:
                try:
                    records.append({
                        "season":   int(clean[0]),
                        "team_fg":  clean[1],
                        "pf_basic": float(clean[2]) if clean[2] else None,   # 5yr smoothed
                        "pf_3yr":   float(clean[3]) if clean[3] else None,
                        "pf_1yr":   float(clean[4]) if clean[4] else None,   # current season
                        "pf_1b":    float(clean[5]) if clean[5] else None,
                        "pf_2b":    float(clean[6]) if len(clean) > 6 and clean[6] else None,
                        "pf_3b":    float(clean[7]) if len(clean) > 7 and clean[7] else None,
                        "pf_hr":    float(clean[8]) if len(clean) > 8 and clean[8] else None,
                    })
                except (ValueError, IndexError):
                    continue

    df = pd.DataFrame(records)
    if df.empty:
        print(f"  [warn] No park factor rows parsed for {season}")
        return df

    # Map team names to standard abbreviations
    df["team"] = df["team_fg"].map(TEAM_MAP)
    unmapped = df[df["team"].isna()]["team_fg"].unique()
    if len(unmapped):
        print(f"  [warn] Unmapped teams for {season}: {unmapped}")

    print(f"  Season {season}: {len(df)} teams parsed")
    return df

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    all_rows = []
    for season in [2024, 2025, 2026]:
        print(f"Fetching park factors for {season}...")
        df = fetch_park_factors_for_year(season)
        if not df.empty:
            all_rows.append(df)
        time.sleep(1.0)

    if not all_rows:
        print("ERROR: no park factor data fetched")
        raise SystemExit(1)

    combined = pd.concat(all_rows, ignore_index=True)
    combined = combined[["season", "team", "team_fg", "pf_basic", "pf_1yr", "pf_hr",
                          "pf_1b", "pf_2b", "pf_3b"]].dropna(subset=["team"])
    combined.to_csv(OUT, index=False)
    print(f"\nSaved {len(combined)} rows -> {OUT}")
    print(combined.groupby("season").size().to_string())

if __name__ == "__main__":
    main()
