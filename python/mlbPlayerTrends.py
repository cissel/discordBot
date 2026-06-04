#!/usr/bin/env python3
"""
mlbPlayerTrends.py
Usage: python mlbPlayerTrends.py "Player Name"

Searches batter_game_logs.csv and pitcher_game_logs.csv for the given player,
computes last-7-game and season-long stats, and writes playerTrends.csv.
"""

import sys
import os
import unicodedata
import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────────────
BASE       = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy")
BATTER_CSV = os.path.join(BASE, "playerData", "batter_game_logs.csv")
PITCHER_CSV= os.path.join(BASE, "playerData", "pitcher_game_logs.csv")
OUT_CSV    = os.path.join(BASE, "playerTrends.csv")

N_RECENT = 7   # "last 7 games" window


def _normalize(s: str) -> str:
    """Lowercase + strip accents so 'Acuna' matches 'Acuña'."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def fuzzy_match(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Case-insensitive, accent-insensitive substring match: query in name OR name in query."""
    q = _normalize(query.strip())
    mask = df["player_name"].apply(
        lambda name: (lambda n: q in n or n in q)(_normalize(name))
    )
    return df[mask]


def era_from_rows(df: pd.DataFrame) -> float:
    """Compute ERA as (sum ER / sum IP) * 9, guarding against 0 IP."""
    total_ip = pd.to_numeric(df["IP"], errors="coerce").sum()
    total_er = pd.to_numeric(df["ER"], errors="coerce").sum()
    if total_ip == 0:
        return float("nan")
    return round((total_er / total_ip) * 9, 4)


def compute_batter_stats(df: pd.DataFrame) -> dict:
    """Return season + last-7 batter stat dict."""
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df.sort_values("game_date")

    for col in ["fantasy_pts", "HR", "RBI", "H", "wOBA"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    last7 = df.tail(N_RECENT)

    return {
        # season
        "games_total":    len(df),
        "avg_pts_season": round(df["fantasy_pts"].mean(), 4),
        "hr_season":      int(df["HR"].sum()),
        "rbi_season":     int(df["RBI"].sum()),
        "h_season":       int(df["H"].sum()),
        "woba_season":    round(df["wOBA"].mean(), 4),
        # last 7
        "games_last7":    len(last7),
        "avg_pts_last7":  round(last7["fantasy_pts"].mean(), 4),
        "hr_last7":       int(last7["HR"].sum()),
        "rbi_last7":      int(last7["RBI"].sum()),
        "h_last7":        int(last7["H"].sum()),
        "woba_last7":     round(last7["wOBA"].mean(), 4),
    }


def compute_pitcher_stats(df: pd.DataFrame) -> dict:
    """Return season + last-7 pitcher stat dict."""
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df.sort_values("game_date")

    for col in ["fantasy_pts", "K", "W", "IP", "ER"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    last7 = df.tail(N_RECENT)

    return {
        # season
        "games_total":    len(df),
        "avg_pts_season": round(df["fantasy_pts"].mean(), 4),
        "k_season":       int(df["K"].sum()),
        "w_season":       int(df["W"].sum()),
        "era_season":     era_from_rows(df),
        # last 7
        "games_last7":    len(last7),
        "avg_pts_last7":  round(last7["fantasy_pts"].mean(), 4),
        "k_last7":        int(last7["K"].sum()),
        "w_last7":        int(last7["W"].sum()),
        "era_last7":      era_from_rows(last7),
    }


def pts_trend(avg_season: float, avg_last7: float) -> float:
    """Percent change from season avg to last-7 avg."""
    if avg_season == 0 or pd.isna(avg_season):
        return float("nan")
    return round(((avg_last7 - avg_season) / abs(avg_season)) * 100, 2)


def main():
    if len(sys.argv) < 2:
        print("error: no player name provided", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]

    # ── load CSVs ──────────────────────────────────────────────────────────────
    try:
        batters = pd.read_csv(BATTER_CSV)
    except FileNotFoundError:
        print(f"error: batter file not found: {BATTER_CSV}", file=sys.stderr)
        batters = pd.DataFrame()

    try:
        pitchers = pd.read_csv(PITCHER_CSV)
    except FileNotFoundError:
        print(f"error: pitcher file not found: {PITCHER_CSV}", file=sys.stderr)
        pitchers = pd.DataFrame()

    # ── fuzzy search ───────────────────────────────────────────────────────────
    batter_match  = fuzzy_match(batters,  query) if not batters.empty  else pd.DataFrame()
    pitcher_match = fuzzy_match(pitchers, query) if not pitchers.empty else pd.DataFrame()

    if batter_match.empty and pitcher_match.empty:
        print("error: player not found")
        sys.exit(1)

    rows = []

    # ── batter hit ─────────────────────────────────────────────────────────────
    if not batter_match.empty:
        canonical = batter_match["player_name"].iloc[0]
        team      = batter_match["team"].iloc[-1]
        fpos      = batter_match["fantasy_position"].iloc[0]
        stats     = compute_batter_stats(batter_match)

        row = {
            "player_name":     canonical,
            "player_type":     "batter",
            "games_total":     stats["games_total"],
            "games_last7":     stats["games_last7"],
            "avg_pts_season":  stats["avg_pts_season"],
            "avg_pts_last7":   stats["avg_pts_last7"],
            "pts_trend_pct":   pts_trend(stats["avg_pts_season"], stats["avg_pts_last7"]),
            # batter-specific
            "hr_last7":        stats["hr_last7"],
            "rbi_last7":       stats["rbi_last7"],
            "h_last7":         stats["h_last7"],
            "woba_last7":      stats["woba_last7"],
            "hr_season":       stats["hr_season"],
            "rbi_season":      stats["rbi_season"],
            "woba_season":     stats["woba_season"],
            # pitcher columns empty
            "k_last7":         "",
            "w_last7":         "",
            "era_last7":       "",
            "k_season":        "",
            "w_season":        "",
            "era_season":      "",
        }
        rows.append(row)
        player_type = "batter"

    # ── pitcher hit ────────────────────────────────────────────────────────────
    if not pitcher_match.empty:
        canonical = pitcher_match["player_name"].iloc[0]
        stats     = compute_pitcher_stats(pitcher_match)

        row = {
            "player_name":     canonical,
            "player_type":     "pitcher",
            "games_total":     stats["games_total"],
            "games_last7":     stats["games_last7"],
            "avg_pts_season":  stats["avg_pts_season"],
            "avg_pts_last7":   stats["avg_pts_last7"],
            "pts_trend_pct":   pts_trend(stats["avg_pts_season"], stats["avg_pts_last7"]),
            # pitcher-specific
            "k_last7":         stats["k_last7"],
            "w_last7":         stats["w_last7"],
            "era_last7":       stats["era_last7"],
            "k_season":        stats["k_season"],
            "w_season":        stats["w_season"],
            "era_season":      stats["era_season"],
            # batter columns empty
            "hr_last7":        "",
            "rbi_last7":       "",
            "h_last7":         "",
            "woba_last7":      "",
            "hr_season":       "",
            "rbi_season":      "",
            "woba_season":     "",
        }
        rows.append(row)
        player_type = "pitcher"

    # If found in both (two-way player like Ohtani), label accordingly
    if not batter_match.empty and not pitcher_match.empty:
        player_type = "batter+pitcher"

    # ── write output ───────────────────────────────────────────────────────────
    COLS = [
        "player_name", "player_type", "games_total", "games_last7",
        "avg_pts_season", "avg_pts_last7", "pts_trend_pct",
        "hr_last7", "rbi_last7", "h_last7", "woba_last7",
        "hr_season", "rbi_season", "woba_season",
        "k_last7", "w_last7", "era_last7",
        "k_season", "w_season", "era_season",
    ]
    out_df = pd.DataFrame(rows, columns=COLS)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    out_df.to_csv(OUT_CSV, index=False)

    print(f"ok: {player_type}")


if __name__ == "__main__":
    main()
