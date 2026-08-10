#!/usr/bin/env python3
"""
fetchDayOfLineups.py
====================
Fetches confirmed batting lineups and probable pitchers for today's MLB games.
Run at ~12:00 PM ET daily (most lineups posted by 11am ET).

Outputs:
  outputs/sports/mlb/fantasy/playerData/day_of_lineups.csv
    columns: game_date, team, player_name, mlbam_id, batting_order, is_home,
             opponent, probable_sp_id, probable_sp_name

  outputs/sports/mlb/fantasy/playerData/probable_pitchers.csv
    columns: game_date, team, mlbam_id, player_name, is_home, opponent
"""

import os
import json
import datetime
import requests
import pandas as pd

BASE_URL  = "https://statsapi.mlb.com/api/v1"
DATA_DIR  = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/playerData")
os.makedirs(DATA_DIR, exist_ok=True)

LINEUP_PATH  = os.path.join(DATA_DIR, "day_of_lineups.csv")
PITCHER_PATH = os.path.join(DATA_DIR, "probable_pitchers.csv")

TODAY = datetime.date.today().strftime("%Y-%m-%d")


def fetch_schedule(date_str):
    """Fetch today's schedule with probablePitcher hydration."""
    url = f"{BASE_URL}/schedule"
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "probablePitcher"
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    games = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))
    return games


def fetch_boxscore(game_pk):
    """Fetch boxscore for a game — returns battingOrder arrays."""
    url = f"{BASE_URL}/game/{game_pk}/boxscore"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def parse_games(games):
    lineup_rows  = []
    pitcher_rows = []

    for game in games:
        game_pk   = game["gamePk"]
        game_date = game.get("officialDate", TODAY)
        teams     = game.get("teams", {})

        # ── probable pitchers ─────────────────────────────────────────────────
        for side in ["away", "home"]:
            team_info = teams.get(side, {})
            pp = team_info.get("probablePitcher")
            if pp:
                opponent_side = "home" if side == "away" else "away"
                opp_team      = teams.get(opponent_side, {}).get("team", {}).get("name", "")
                pitcher_rows.append({
                    "game_date":    game_date,
                    "game_pk":      game_pk,
                    "team":         team_info.get("team", {}).get("name", ""),
                    "team_id":      team_info.get("team", {}).get("id"),
                    "is_home":      1 if side == "home" else 0,
                    "opponent":     opp_team,
                    "mlbam_id":     pp.get("id"),
                    "player_name":  pp.get("fullName", ""),
                })

        # ── confirmed lineups from boxscore ───────────────────────────────────
        try:
            bs = fetch_boxscore(game_pk)
        except Exception as e:
            print(f"  [warn] boxscore fetch failed for {game_pk}: {e}")
            continue

        bs_teams = bs.get("teams", {})
        for side in ["away", "home"]:
            team_data     = bs_teams.get(side, {})
            batting_order = team_data.get("battingOrder", [])
            players       = team_data.get("players", {})

            if not batting_order:
                continue  # lineup not posted yet

            team_name = team_data.get("team", {}).get("name", "")
            opponent_side = "home" if side == "away" else "away"
            opp_name  = bs_teams.get(opponent_side, {}).get("team", {}).get("name", "")

            # probable SP for the opponent (the pitcher this batter faces)
            opp_sp = next(
                (p for p in pitcher_rows
                 if p["game_pk"] == game_pk and p["is_home"] == (1 if side == "away" else 0)),
                None
            )

            for order_pos, player_id in enumerate(batting_order, start=1):
                player_key  = f"ID{player_id}"
                player_info = players.get(player_key, {})
                person      = player_info.get("person", {})
                lineup_rows.append({
                    "game_date":        game_date,
                    "game_pk":          game_pk,
                    "team":             team_name,
                    "team_id":          team_data.get("team", {}).get("id"),
                    "is_home":          1 if side == "home" else 0,
                    "opponent":         opp_name,
                    "mlbam_id":         player_id,
                    "player_name":      person.get("fullName", ""),
                    "batting_order":    order_pos,
                    "position":         player_info.get("position", {}).get("abbreviation", ""),
                    "probable_sp_id":   opp_sp["mlbam_id"] if opp_sp else None,
                    "probable_sp_name": opp_sp["player_name"] if opp_sp else "",
                })

    return lineup_rows, pitcher_rows


def main():
    print(f"[fetchDayOfLineups] date={TODAY}")

    games = fetch_schedule(TODAY)
    print(f"  found {len(games)} games")

    lineup_rows, pitcher_rows = parse_games(games)

    # ── write probable pitchers ───────────────────────────────────────────────
    pp_df = pd.DataFrame(pitcher_rows)
    pp_df.to_csv(PITCHER_PATH, index=False)
    print(f"  probable pitchers: {len(pp_df)} rows -> {PITCHER_PATH}")

    # ── write lineups ─────────────────────────────────────────────────────────
    if lineup_rows:
        lu_df = pd.DataFrame(lineup_rows)
        lu_df.to_csv(LINEUP_PATH, index=False)
        print(f"  confirmed lineups: {len(lu_df)} batters -> {LINEUP_PATH}")
    else:
        # Write empty file with correct header so downstream reads don't break
        pd.DataFrame(columns=[
            "game_date", "game_pk", "team", "team_id", "is_home", "opponent",
            "mlbam_id", "player_name", "batting_order", "position",
            "probable_sp_id", "probable_sp_name"
        ]).to_csv(LINEUP_PATH, index=False)
        print(f"  no confirmed lineups yet (early run) -> empty file written")

    print("[fetchDayOfLineups] done.")


if __name__ == "__main__":
    main()
