#!/usr/bin/env python3
"""
nflTeamRosterRisk.py

Companion to r/nflTeamRosterRisk.R for the /nfl teamrisk Discord command.
Pulls the current Room 40 Sleeper roster for ONE selected team (every
rostered player regardless of starter/bench/IR slot - "who does this GM
own", not "who they started") and writes a CSV the R script joins against
nflreadr weekly stats to build a mean-vs-volatility risk scatter, same
visual language as nflFreeAgentRisk.R / James's SPY & MLB Sharpe work.

Team selection: matches against each roster owner's Sleeper team_name
(falls back to display_name if team_name is blank), case-insensitive
substring match. Defaults to James's own team, "drunken sailors".

IMPORTANT: Room 40's Sleeper league_id rolls over every season - resolved
live via the commissioner's (jhcv) user_id, same pattern as
nflFreeAgents.py. Do not hardcode a cached league_id.

Output: outputs/sports/nfl/fantasy/team_roster_<slug>.csv
Columns: sleeper_id, full_name, position, team, gsis_id, season, team_name

Usage: venv/bin/python3 python/nflTeamRosterRisk.py ["team name substring"]
"""
import os
import re
import sys

import pandas as pd
import requests

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
os.makedirs(OUT_DIR, exist_ok=True)

BASE = "https://api.sleeper.app/v1"
JHCV_USER_ID = "1003075364270313472"  # James's Sleeper user_id (stable across seasons)
DEFAULT_TEAM = "drunken sailors"      # James's own Room 40 team

# Sleeper team abbreviation -> nflreadr/nflfastR team abbreviation (differ
# only for the Rams; every other code lines up between the two sources).
TEAM_ABBR_FIX = {"LAR": "LA"}

RELEVANT_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

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
    team_query = sys.argv[1].strip().lower() if len(sys.argv) > 1 and sys.argv[1].strip() else DEFAULT_TEAM

    league_id, season = resolve_league_id()
    print(f"Room 40 league_id (season {season}): {league_id}")

    users = requests.get(f"{BASE}/league/{league_id}/users", timeout=20).json()
    user_map = {
        u["user_id"]: {
            "display_name": u.get("display_name", ""),
            "team_name": u.get("metadata", {}).get("team_name") or u.get("display_name", ""),
        }
        for u in users
    }

    rosters = requests.get(f"{BASE}/league/{league_id}/rosters", timeout=20).json()

    match = None
    for r in rosters:
        u = user_map.get(r.get("owner_id"), {})
        candidates = [u.get("team_name", ""), u.get("display_name", "")]
        if any(team_query in c.lower() for c in candidates if c):
            match = (r, u)
            break

    if match is None:
        available = sorted({u["team_name"] for u in user_map.values() if u["team_name"]})
        raise SystemExit(
            f"No Room 40 team matched '{team_query}'. Available teams: {', '.join(available)}"
        )

    roster, owner = match
    team_name = owner["team_name"]
    player_ids = roster.get("players") or []
    print(f"Matched team: '{team_name}' (owner: {owner.get('display_name')}) - {len(player_ids)} rostered players")

    pool = requests.get(f"{BASE}/players/nfl", timeout=60).json()
    crosswalk = fetch_crosswalk()

    rows = []
    for pid in player_ids:
        p = pool.get(pid)
        if p is None:
            # DEF entries: player_id IS the team abbreviation, not in the player pool dict
            if re.fullmatch(r"[A-Z]{2,4}", pid or ""):
                rows.append({
                    "sleeper_id": pid, "full_name": pid, "position": "DEF",
                    "team": TEAM_ABBR_FIX.get(pid, pid), "gsis_id": "",
                    "season": season, "team_name": team_name,
                })
            continue
        pos = p.get("position")
        if pos not in RELEVANT_POSITIONS:
            continue
        if pos == "DEF":
            gsis_id = ""
            team = TEAM_ABBR_FIX.get(pid, pid)
            full_name = p.get("full_name") or pid
        else:
            gsis_id = crosswalk.get(str(pid), "")
            team = TEAM_ABBR_FIX.get(p.get("team"), p.get("team"))
            full_name = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        rows.append({
            "sleeper_id": pid, "full_name": full_name, "position": pos,
            "team": team, "gsis_id": gsis_id, "season": season, "team_name": team_name,
        })

    df = pd.DataFrame(rows)
    slug = re.sub(r"[^a-z0-9]+", "_", team_name.lower()).strip("_")
    out_path = os.path.join(OUT_DIR, f"team_roster_{slug}.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rostered players ('{team_name}') -> {out_path}")
    missing_gsis = ((df["gsis_id"] == "") & (df["position"] != "DEF")).sum() if len(df) else 0
    print(f"  (no gsis_id crosswalk match: {missing_gsis} - these drop out of the stats join downstream)")
    print(f"OUT_PATH={out_path}")
    print(f"TEAM_NAME={team_name}")


if __name__ == "__main__":
    main()
