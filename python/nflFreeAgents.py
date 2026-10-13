#!/usr/bin/env python3
"""
nflFreeAgents.py

Pulls the current Room 40 Sleeper roster and the full Sleeper player pool,
then writes out the list of players (by position scope) who are NOT on any
Room 40 roster this week - i.e. actual waiver-wire free agents, for the
/nfl fantasyfreeagent Discord command.

IMPORTANT: Room 40's Sleeper league_id rolls over every season (new league
created, linked via previous_league_id) - do not hardcode a cached ID from a
prior year without verifying. Resolved here via the commissioner's
(jhcv) user_id -> GET /user/{uid}/leagues/nfl/{season}, same pattern used
elsewhere in this repo.

Position scopes (mirrors Sleeper roster slots + what James asked for):
  qb    -> QB
  rb    -> RB
  wr    -> WR
  te    -> TE
  flex  -> RB + WR + TE (anyone free-agent-eligible for the FLEX slot)
  k     -> K (kicker)
  dst   -> DEF (team defense/special teams)

Output: outputs/sports/nfl/fantasy/free_agents_<scope>.csv
Columns: sleeper_id, full_name, position, team, gsis_id, season
  (gsis_id is the nflverse player id used to join against nflreadr stats in
  the companion R script nflFreeAgentRisk.R; DST rows use team abbreviation
  as the join key instead and leave gsis_id blank.)

Usage: venv/bin/python3 python/nflFreeAgents.py <scope>
"""
import os
import sys

import pandas as pd
import requests

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
os.makedirs(OUT_DIR, exist_ok=True)

BASE = "https://api.sleeper.app/v1"
JHCV_USER_ID = "1003075364270313472"  # James's Sleeper user_id (stable across seasons)

# Sleeper team abbreviation -> nflreadr/nflfastR team abbreviation (differ
# only for the Rams; every other code lines up between the two sources).
TEAM_ABBR_FIX = {"LAR": "LA"}

POSITION_GROUPS = {
    "qb":   ["QB"],
    "rb":   ["RB"],
    "wr":   ["WR"],
    "te":   ["TE"],
    "flex": ["RB", "WR", "TE"],
    "k":    ["K"],
    "dst":  ["DEF"],
}

CROSSWALK_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"


def resolve_league_id():
    """Current-season Room 40 league_id, resolved live (never trust a cached
    ID across season boundaries - Sleeper issues a new league_id every year
    and links the old one via previous_league_id)."""
    state = requests.get(f"{BASE}/state/nfl", timeout=20).json()
    season = state.get("season")
    leagues = requests.get(f"{BASE}/user/{JHCV_USER_ID}/leagues/nfl/{season}", timeout=20).json()
    for lg in leagues:
        if lg.get("name") == "Room 40":
            return lg["league_id"], season
    raise SystemExit(f"Could not find a 'Room 40' league for user {JHCV_USER_ID} in season {season}")


def fetch_rostered_player_ids(league_id):
    rosters = requests.get(f"{BASE}/league/{league_id}/rosters", timeout=20).json()
    rostered = set()
    for r in rosters:
        rostered.update(r.get("players") or [])
    return rostered


def fetch_player_pool():
    print("Fetching Sleeper player pool (~14MB)...")
    r = requests.get(f"{BASE}/players/nfl", timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_crosswalk():
    """sleeper_id -> gsis_id map (dynastyprocess community file, same source
    already proven reliable for nflverse <-> platform ID joins)."""
    try:
        df = pd.read_csv(CROSSWALK_URL, low_memory=False)
        df = df.dropna(subset=["sleeper_id"])
        df["sleeper_id"] = df["sleeper_id"].astype(float).astype(int).astype(str)
        return dict(zip(df["sleeper_id"], df["gsis_id"]))
    except Exception as e:
        print(f"WARNING: crosswalk fetch failed ({e}); gsis_id will be blank", file=sys.stderr)
        return {}


def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in POSITION_GROUPS:
        print(f"Usage: {sys.argv[0]} <{'|'.join(POSITION_GROUPS)}>")
        sys.exit(1)
    scope = sys.argv[1].lower()
    positions = POSITION_GROUPS[scope]

    league_id, season = resolve_league_id()
    print(f"Room 40 league_id (season {season}): {league_id}")

    rostered = fetch_rostered_player_ids(league_id)
    print(f"Rostered player slots league-wide: {len(rostered)}")

    pool = fetch_player_pool()
    crosswalk = fetch_crosswalk() if scope != "dst" else {}

    rows = []
    for pid, p in pool.items():
        if p.get("position") not in positions:
            continue
        if pid in rostered:
            continue
        if scope != "dst":
            # skill positions / kicker: require an active roster spot on an
            # NFL team (filters out retired/practice-squad/injured-reserve-
            # stashed Sleeper entries that otherwise clutter the FA pool)
            if p.get("status") != "Active" or not p.get("team"):
                continue
            gsis_id = crosswalk.get(str(pid), "")
            team = p.get("team")
        else:
            # DEF entries: player_id IS the team abbreviation
            gsis_id = ""
            team = TEAM_ABBR_FIX.get(pid, pid)
        rows.append({
            "sleeper_id":  pid,
            "full_name":   p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position":    p.get("position"),
            "team":        TEAM_ABBR_FIX.get(team, team),
            "gsis_id":     gsis_id,
            "season":      season,
        })

    df = pd.DataFrame(rows)
    out_path = os.path.join(OUT_DIR, f"free_agents_{scope}.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} free agent candidates ({scope}) -> {out_path}")
    if scope != "dst":
        missing_gsis = (df["gsis_id"] == "").sum() if len(df) else 0
        print(f"  (no gsis_id crosswalk match: {missing_gsis} - these drop out of the stats join downstream)")


if __name__ == "__main__":
    main()
