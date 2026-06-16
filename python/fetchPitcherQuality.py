#!/usr/bin/env python3
"""
fetchPitcherQuality.py
======================
Pulls season-level pitcher quality stats for all SP/RP in the current player pool,
plus today's probable starters. Used as features in the batter prediction model
("how hard is the pitcher the batter is facing today?").

Sources:
  - MLB Stats API: ERA, WHIP, K/9, BB/9, HR/9, strikeout/walk ratio (always works)
  - Savant statcast_pitcher_expected_stats: xERA, est_wOBA (via pybaseball)

Output: outputs/sports/mlb/fantasy/playerData/pitcher_quality.csv
Columns: player_name, mlbam_id, season, era, whip, k_per9, bb_per9,
         hr_per9, k_bb_ratio, xera, est_woba, quality_score

quality_score: z-score composite (higher = tougher for batters to face)
  = z(era_inverse) + z(whip_inverse) + z(k_per9) + z(xera_inverse)

Run nightly so pitcher quality reflects current season performance.
"""

import os, sys, time, requests
import pandas as pd
import numpy as np

BASE    = os.path.expanduser("~/discordBot")
POOL    = os.path.join(BASE, "outputs/sports/mlb/fantasy/playerData/player_pool.csv")
OUT     = os.path.join(BASE, "outputs/sports/mlb/fantasy/playerData/pitcher_quality.csv")
MLB_API = "https://statsapi.mlb.com/api/v1"

import datetime
SEASON = datetime.date.today().year

def fetch_pitcher_stats_mlbapi(mlbam_id: int) -> dict:
    """Pull current season stats for one pitcher from MLB Stats API."""
    url = f"{MLB_API}/people/{mlbam_id}/stats"
    params = {
        "stats": "season",
        "season": SEASON,
        "sportId": 1,
        "group": "pitching",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0]["stat"]
        return {
            "era":          float(s.get("era",              0) or 0),
            "whip":         float(s.get("whip",             0) or 0),
            "k_per9":       float(s.get("strikeOutsPer9Inn",0) or 0),
            "bb_per9":      float(s.get("walksPer9Inn",     0) or 0),
            "hr_per9":      float(s.get("homeRunsPer9",     0) or 0),
            "k_bb_ratio":   float(s.get("strikeoutWalkRatio", 0) or 0),
            "innings_pitched": float(s.get("inningsPitched", 0) or 0),
        }
    except Exception:
        return {}

def fetch_savant_xera(season: int) -> pd.DataFrame:
    """Pull xERA and est_wOBA from Baseball Savant via pybaseball."""
    try:
        from pybaseball import statcast_pitcher_expected_stats
        df = statcast_pitcher_expected_stats(season)
        if df is None or df.empty:
            return pd.DataFrame()
        # Rename to match our schema
        keep = {"player_id": "mlbam_id", "era": "savant_era",
                "xera": "xera", "est_woba": "est_woba"}
        available = {k: v for k, v in keep.items() if k in df.columns}
        df = df.rename(columns=available)[list(available.values())]
        df["mlbam_id"] = pd.to_numeric(df["mlbam_id"], errors="coerce")
        return df.dropna(subset=["mlbam_id"])
    except Exception as e:
        print(f"  [warn] Savant xERA fetch failed: {e}")
        return pd.DataFrame()

def z_score(series: pd.Series) -> pd.Series:
    std = series.std()
    if std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    if not os.path.exists(POOL):
        print(f"ERROR: player_pool.csv not found at {POOL}")
        sys.exit(1)

    pool = pd.read_csv(POOL)
    pitchers = pool[pool["fantasy_position"].isin(["SP", "RP"])].copy()
    pitchers["mlbam_id"] = pd.to_numeric(pitchers["mlbam_id"], errors="coerce")
    pitchers = pitchers.dropna(subset=["mlbam_id"])
    print(f"Fetching pitcher quality for {len(pitchers)} pitchers (season {SEASON})...")

    records = []
    for i, row in pitchers.iterrows():
        mlbam_id = int(row["mlbam_id"])
        name     = row["PlayerName"]
        if (i - pitchers.index[0]) % 20 == 0:
            idx = list(pitchers.index).index(i)
            print(f"  {idx}/{len(pitchers)} - {name}")
        stats = fetch_pitcher_stats_mlbapi(mlbam_id)
        if not stats or stats.get("innings_pitched", 0) < 5:
            continue
        records.append({
            "player_name":       name,
            "mlbam_id":          mlbam_id,
            "fantasy_position":  row["fantasy_position"],
            "season":            SEASON,
            **stats,
        })
        time.sleep(0.05)  # gentle rate limit

    if not records:
        print("ERROR: no pitcher stats fetched")
        sys.exit(1)

    df = pd.DataFrame(records)
    print(f"  Fetched stats for {len(df)} pitchers")

    # Join Savant xERA
    print("  Fetching Savant xERA...")
    savant = fetch_savant_xera(SEASON)
    if not savant.empty:
        df = df.merge(savant, on="mlbam_id", how="left")
        print(f"  Joined xERA for {df['xera'].notna().sum()} pitchers")
    else:
        df["xera"]     = np.nan
        df["est_woba"] = np.nan

    # ── quality_score: composite difficulty metric (higher = harder to face) ──
    # Invert metrics where lower = better for the pitcher
    df["era_inv"]  = -df["era"].clip(lower=0)
    df["whip_inv"] = -df["whip"].clip(lower=0)
    df["xera_inv"] = -df["xera"].clip(lower=0).fillna(-df["era_inv"])

    components = ["era_inv", "whip_inv", "k_per9", "xera_inv"]
    available  = [c for c in components if c in df.columns and df[c].notna().sum() > 5]
    z_sum = sum(z_score(df[c]) for c in available)
    df["quality_score"] = (z_sum / len(available)).round(4)

    # Final output
    out_cols = ["player_name", "mlbam_id", "fantasy_position", "season",
                "era", "whip", "k_per9", "bb_per9", "hr_per9", "k_bb_ratio",
                "innings_pitched", "xera", "est_woba", "quality_score"]
    out_cols = [c for c in out_cols if c in df.columns]
    df[out_cols].to_csv(OUT, index=False)
    print(f"\nSaved {len(df)} pitchers -> {OUT}")

    # Quick preview
    print("\nTop 10 hardest to face (by quality_score):")
    print(df[["player_name", "era", "whip", "k_per9", "xera", "quality_score"]]
          .sort_values("quality_score", ascending=False).head(10).to_string(index=False))

if __name__ == "__main__":
    main()
