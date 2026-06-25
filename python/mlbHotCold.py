#!/usr/bin/env python3
"""
mlbHotCold.py
No arguments required.

Reads batter_game_logs.csv and pitcher_game_logs.csv, computes last-7-game
vs season fantasy_pts averages, and writes hot/cold CSVs for batters and pitchers.

Filters:
  - at least 5 total games
  - at least 3 games in the last 7 (by date rank per player)

Hot batters:  top 8 by avg_pts_last7
Cold batters: bottom 5, among those with season avg >= 3.0
Hot pitchers: top 5 by avg_pts_last7
Cold pitchers:bottom 3, among those with season avg >= 10.0
"""

import os
import sys
import pandas as pd

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.expanduser("~/discordBot/python"))
try:
    from predictFantasy import get_ml_scores as _get_ml_scores
except Exception:
    _get_ml_scores = None

# ── paths ──────────────────────────────────────────────────────────────────────
BASE        = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy")
BATTER_CSV  = os.path.join(BASE, "playerData", "batter_game_logs.csv")
PITCHER_CSV = os.path.join(BASE, "playerData", "pitcher_game_logs.csv")

OUT_HOT_BAT  = os.path.join(BASE, "hotBatters.csv")
OUT_COLD_BAT = os.path.join(BASE, "coldBatters.csv")
OUT_HOT_PIT  = os.path.join(BASE, "hotPitchers.csv")
OUT_COLD_PIT = os.path.join(BASE, "coldPitchers.csv")

N_RECENT         = 7
MIN_TOTAL_GAMES  = 5
MIN_LAST7_GAMES  = 3
COLD_BAT_MIN_AVG = 3.0
COLD_PIT_MIN_AVG = 10.0

OUT_COLS = [
    "player_name", "team", "fantasy_position",
    "avg_pts_last7", "avg_pts_season", "trend_pct", "games_last7",
]


def load_csv(path: str, label: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
        if df.empty:
            print(f"warning: {label} CSV is empty ({path})", file=sys.stderr)
        return df
    except FileNotFoundError:
        print(f"error: {label} file not found: {path}", file=sys.stderr)
        return pd.DataFrame()


def trend_pct(avg_season: float, avg_last7: float) -> float:
    if avg_season == 0 or pd.isna(avg_season):
        return float("nan")
    return round(((avg_last7 - avg_season) / abs(avg_season)) * 100, 2)


def build_player_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each player in df, compute:
      - games_total
      - games_last7   (count of rows in their N_RECENT most-recent games)
      - avg_pts_season
      - avg_pts_last7
      - trend_pct
      - team, fantasy_position  (from most recent game)

    Returns a DataFrame with one row per player, filtered to qualifying players.
    """
    df = df.copy()
    df["game_date"]   = pd.to_datetime(df["game_date"], errors="coerce")
    df["fantasy_pts"] = pd.to_numeric(df["fantasy_pts"], errors="coerce")

    # Sort so tail() gives us the most recent games
    df = df.sort_values(["player_name", "game_date"])

    records = []
    for player, grp in df.groupby("player_name", sort=False):
        games_total = len(grp)
        if games_total < MIN_TOTAL_GAMES:
            continue

        last7 = grp.tail(N_RECENT)
        games_last7 = len(last7)
        if games_last7 < MIN_LAST7_GAMES:
            continue

        avg_season = grp["fantasy_pts"].mean()
        avg_l7     = last7["fantasy_pts"].mean()

        records.append({
            "player_name":     player,
            "team":            grp["team"].iloc[-1],
            "fantasy_position":grp["fantasy_position"].iloc[0],
            "games_total":     games_total,
            "games_last7":     games_last7,
            "avg_pts_season":  round(avg_season, 4),
            "avg_pts_last7":   round(avg_l7,     4),
            "trend_pct":       trend_pct(avg_season, avg_l7),
        })

    if not records:
        return pd.DataFrame(columns=OUT_COLS + ["games_total"])

    return pd.DataFrame(records)


def main():
    os.makedirs(BASE, exist_ok=True)

    _ml_bat = _get_ml_scores("batters", ("daily", "weekly")) if _get_ml_scores else {}
    _ml_pit = _get_ml_scores("pitchers", ("daily", "weekly")) if _get_ml_scores else {}
    import unicodedata as _ud
    def _norm(s):
        return _ud.normalize("NFD", str(s)).encode("ascii","ignore").decode().strip().lower()
    def _attach_ml(df, ml_dict):
        df["ml_pts_daily"]  = df["player_name"].apply(lambda n: ml_dict.get(_norm(n), {}).get("ml_pts_daily"))
        df["ml_pts_weekly"] = df["player_name"].apply(lambda n: ml_dict.get(_norm(n), {}).get("ml_pts_weekly"))
        return df

    bat_raw = load_csv(BATTER_CSV,  "batter")
    pit_raw = load_csv(PITCHER_CSV, "pitcher")

    errors = []

    # ── BATTERS ────────────────────────────────────────────────────────────────
    if not bat_raw.empty:
        bat_summary = build_player_summary(bat_raw)

        if bat_summary.empty:
            print("warning: no qualifying batters found", file=sys.stderr)
        else:
            # Hot batters: top 8 by avg_pts_last7
            hot_bat = (
                bat_summary
                .sort_values("avg_pts_last7", ascending=False)
                .head(8)
                [OUT_COLS]
                .reset_index(drop=True)
            )
            hot_bat = _attach_ml(hot_bat, _ml_bat)
            hot_bat.to_csv(OUT_HOT_BAT, index=False)

            # Cold batters: bottom 5 among those with season avg >= 3.0
            cold_pool = bat_summary[bat_summary["avg_pts_season"] >= COLD_BAT_MIN_AVG]
            cold_bat = (
                cold_pool
                .sort_values("avg_pts_last7", ascending=True)
                .head(5)
                [OUT_COLS]
                .reset_index(drop=True)
            )
            cold_bat = _attach_ml(cold_bat, _ml_bat)
            cold_bat.to_csv(OUT_COLD_BAT, index=False)
    else:
        errors.append("batter data unavailable")

    # ── PITCHERS ───────────────────────────────────────────────────────────────
    if not pit_raw.empty:
        pit_summary = build_player_summary(pit_raw)

        if pit_summary.empty:
            print("warning: no qualifying pitchers found", file=sys.stderr)
        else:
            # Hot pitchers: top 5 by avg_pts_last7
            hot_pit = (
                pit_summary
                .sort_values("avg_pts_last7", ascending=False)
                .head(5)
                [OUT_COLS]
                .reset_index(drop=True)
            )
            hot_pit = _attach_ml(hot_pit, _ml_pit)
            hot_pit.to_csv(OUT_HOT_PIT, index=False)

            # Cold pitchers: bottom 3 among those with season avg >= 10.0
            cold_pool = pit_summary[pit_summary["avg_pts_season"] >= COLD_PIT_MIN_AVG]
            cold_pit = (
                cold_pool
                .sort_values("avg_pts_last7", ascending=True)
                .head(3)
                [OUT_COLS]
                .reset_index(drop=True)
            )
            cold_pit = _attach_ml(cold_pit, _ml_pit)
            cold_pit.to_csv(OUT_COLD_PIT, index=False)
    else:
        errors.append("pitcher data unavailable")

    if errors:
        print("error: " + "; ".join(errors), file=sys.stderr)
        sys.exit(1)

    print("ok")


if __name__ == "__main__":
    main()
