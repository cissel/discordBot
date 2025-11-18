# sleeperProj.py
# pip install requests pandas python-dotenv
import os
import json
import requests
import pandas as pd
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))

GRAPHQL_URL = "https://sleeper.com/graphql"
REST_BASE   = "https://api.sleeper.app/v1"

LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID", "").strip()
if not LEAGUE_ID:
    raise SystemExit("Set SLEEPER_LEAGUE_ID in your environment (export SLEEPER_LEAGUE_ID=XXXXXXXXXXXX).")

HEADERS = {"content-type": "application/json"}

def get_current_state():
    r = requests.get(f"{REST_BASE}/state/nfl", timeout=20)
    r.raise_for_status()
    js = r.json()
    # Example keys: {"season":"2025","week":9,"season_type":"regular", ...}
    season = str(js.get("season"))
    week = js.get("week")
    season_type = js.get("season_type") or "regular"
    if not season or week in (None, 0):
        raise SystemExit(f"NFL state not in-season (season={season}, week={week}).")
    return season, int(week), season_type

def get_league_roster_player_ids(league_id: str) -> list[str]:
    r = requests.get(f"{REST_BASE}/league/{league_id}/rosters", timeout=30)
    r.raise_for_status()
    rosters = r.json() or []
    ids = set()
    for ros in rosters:
        # players: active; reserve: IR/NFI etc; taxi: practice
        for key in ("players", "reserve", "taxi"):
            vals = ros.get(key) or []
            for pid in vals:
                if pid:  # sleeper player ids are strings like "6797"
                    ids.add(str(pid))
    if not ids:
        raise SystemExit("No player IDs found on league rosters.")
    return sorted(ids)

def build_graphql_query(season: str, week: int, season_type: str, player_ids: list[str]) -> dict:
    # Sleeper GraphQL requires inline literals; we’ll alias the field so we can find it.
    pid_list = ",".join(f"\"{pid}\"" for pid in player_ids)
    alias_proj = f"nfl__{season_type}__{season}__{week}__proj"
    # If you also want same-week actual stats, you could add a second field with category: "stat".
    query = f"""
    query get_player_score_and_projections_batch {{
      {alias_proj}: stats_for_players_in_week(
        sport: "nfl",
        season: "{season}",
        category: "proj",
        season_type: "{season_type}",
        week: {week},
        player_ids: [{pid_list}]
      ){{
        game_id
        opponent
        player_id
        stats
        team
        week
        season
      }}
    }}
    """
    return {
        "operationName": "get_player_score_and_projections_batch",
        "variables": {},
        "query": " ".join(line.strip() for line in query.splitlines())
    }

def extract_rows_from_proj(data: dict) -> list[dict]:
    # Find the alias that ends with "__proj"
    proj_key = next((k for k in (data.get("data") or {}).keys() if k.endswith("__proj")), None)
    if not proj_key:
        raise SystemExit("Projection block not found in GraphQL response.")
    records = data["data"][proj_key] or []
    def extract_row(rec):
        s = rec.get("stats") or {}
        return {
            "player_id": rec.get("player_id"),
            "team": rec.get("team"),
            "opponent": rec.get("opponent"),
            "season": rec.get("season"),
            "week": rec.get("week"),
            "pts_ppr": s.get("pts_ppr"),
            "adp_dd_ppr": s.get("adp_dd_ppr"),
        }
    return [extract_row(r) for r in records]

def main():
    season, week, season_type = get_current_state()
    player_ids = get_league_roster_player_ids(LEAGUE_ID)

    payload = build_graphql_query(season, week, season_type, player_ids)
    resp = requests.post(GRAPHQL_URL, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("errors"):
        print(json.dumps(data["errors"], indent=2))
        raise SystemExit(1)

    rows = extract_rows_from_proj(data)
    if not rows:
        raise SystemExit("No projection rows returned.")

    df = pd.DataFrame(rows)
    cols = ["player_id","team","opponent","season","week","pts_ppr","adp_dd_ppr"]
    df = df[cols].sort_values(["team","player_id"]).reset_index(drop=True)

    out_path = "outputs/sports/nfl/sleeper_proj_pts.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} rows to {out_path}")
    print(df.head(10).to_string(index=False))

if __name__ == "__main__":
    main()
