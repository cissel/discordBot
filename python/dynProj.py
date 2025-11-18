# dynProj.py
import os
import argparse
import requests
from itertools import islice
import pandas as pd

SLEEPER_LEAGUE_ID = "1259616442014244864"
GRAPHQL_URL = "https://sleeper.com/graphql"

def get_sleeper_state():
    r = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
    r.raise_for_status()
    s = r.json()
    week = int(s.get("week") or 1)
    season_type = str(s.get("season_type") or "regular")
    # Normalize season_type so GraphQL doesn't fall back oddly
    if season_type not in ("regular", "post"):
        season_type = "regular"
    return {
        "season": str(s.get("season")),
        "season_type": season_type,
        "week": week,
    }

def get_league_player_ids(league_id: str) -> list[str]:
    r = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/rosters", timeout=20)
    r.raise_for_status()
    rosters = r.json()
    ids = []
    for team in rosters:
        for key in ("players", "reserve", "taxi"):
            vals = team.get(key) or []
            ids.extend(vals)
    seen = set()
    out = []
    for pid in ids:
        if pid is None:
            continue
        pid = str(pid)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out

def chunks(iterable, size):
    it = iter(iterable)
    while True:
        batch = list(islice(it, size))
        if not batch:
            break
        yield batch

def build_payload(season: str, season_type: str, week: int, player_ids: list[str]) -> dict:
    query = """
      query get_player_score_and_projections_batch(
        $sport: String!,
        $season: String!,
        $season_type: String!,
        $week: Int!,
        $player_ids: [String!]!
      ) {
        stats: stats_for_players_in_week(
          sport: $sport,
          season: $season,
          category: "stat",
          season_type: $season_type,
          week: $week,
          player_ids: $player_ids
        ) {
          game_id
          opponent
          player_id
          stats
          team
          week
          season
        }
        proj: stats_for_players_in_week(
          sport: $sport,
          season: $season,
          category: "proj",
          season_type: $season_type,
          week: $week,
          player_ids: $player_ids
        ) {
          game_id
          opponent
          player_id
          stats
          team
          week
          season
        }
      }
    """.strip()

    variables = {
        "sport": "nfl",
        "season": season,
        "season_type": season_type,  # "regular" or "post"
        "week": int(week),
        "player_ids": player_ids
    }

    return {
        "operationName": "get_player_score_and_projections_batch",
        "variables": variables,
        "query": query
    }

def fetch_batch(player_ids_batch, season, season_type, week):
    payload = build_payload(season, season_type, week, player_ids_batch)
    r = requests.post(GRAPHQL_URL, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]

def fetch_live_projections_for_league(league_id: str, week_override: int | None = None, max_ids_per_call: int = 200):
    state = get_sleeper_state()
    season = state["season"]
    season_type = state["season_type"]
    week = week_override if week_override is not None else int(os.getenv("WEEK", state["week"]))

    player_ids = get_league_player_ids(league_id)

    all_stats = []
    all_proj  = []
    for batch in chunks(player_ids, max_ids_per_call):
        data = fetch_batch(batch, season, season_type, week)
        all_stats.extend(data.get("stats") or [])
        all_proj.extend(data.get("proj") or [])

    return {
        "season": season,
        "season_type": season_type,
        "week": week,
        "stats": all_stats,
        "proj": all_proj,
        "player_count": len(player_ids)
    }

def to_flat_df(records, prefix: str):
    if not records:
        return pd.DataFrame()
    base = pd.DataFrame(records)
    if "stats" in base.columns:
        stats_flat = pd.json_normalize(base["stats"]).add_prefix(f"{prefix}_")
        base = pd.concat([base.drop(columns=["stats"], errors="ignore"), stats_flat], axis=1)
    # ensure consistent join key types
    for col in ["player_id", "week", "season", "team", "game_id", "opponent"]:
        if col in base.columns:
            base[col] = base[col].astype(str)
    return base

def current_week_projections_df(league_id: str, week_override: int | None = None) -> pd.DataFrame:
    res = fetch_live_projections_for_league(league_id, week_override=week_override)
    proj_df = to_flat_df(res.get("proj", []), "proj")
    # Strictly keep rows for the requested/current week only
    wk_str = str(res["week"])
    if "week" in proj_df.columns:
        proj_df = proj_df[proj_df["week"] == wk_str].copy()
    return proj_df

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default=SLEEPER_LEAGUE_ID, help="Sleeper League ID")
    p.add_argument("--week", type=int, default=None, help="Override current NFL week (optional)")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    res = fetch_live_projections_for_league(args.league, week_override=args.week)

    stats_df = to_flat_df(res.get("stats", []), "stat")
    proj_df  = to_flat_df(res.get("proj",  []), "proj")

    # Keep only the exact week we asked for
    wk = str(res["week"])
    if "week" in stats_df.columns:
        stats_df = stats_df[stats_df["week"] == wk].copy()
    if "week" in proj_df.columns:
        proj_df = proj_df[proj_df["week"] == wk].copy()

    # Output
    os.makedirs("~/discordBot/outputs/sports/nfl/fantasy", exist_ok=True)
    stats_path = f"~/discordBot/outputs/sports/nfl/fantasy/current_stats_week{wk}.csv"
    proj_path  = f"~/discordBot/outputs/sports/nfl/fantasy/current_proj_week{wk}.csv"
    stats_df.to_csv(stats_path, index=False)
    proj_df.to_csv(proj_path, index=False)

    print(f"week={wk} season={res['season']} season_type={res['season_type']} players={res['player_count']}")
    print(f"stats rows: {len(stats_df)} | proj rows: {len(proj_df)}")
