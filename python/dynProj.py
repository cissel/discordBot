import requests
from itertools import islice
import pandas as pd

SLEEPER_LEAGUE_ID = "1259616442014244864"
GRAPHQL_URL = "https://sleeper.com/graphql"  

def get_sleeper_state():
    r = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
    r.raise_for_status()
    s = r.json()
    # Examples: s = {"season":"2025","season_type":"regular","week":2, ...}
    return {
        "season": str(s.get("season")),
        "season_type": str(s.get("season_type", "regular")),
        "week": int(s.get("week")),
    }

def get_league_player_ids(league_id: str) -> list[str]:
    """
    Returns a de-duplicated list of *currently rostered* player_ids
    across the league (includes DSTs which are team codes like 'SF').
    """
    r = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/rosters", timeout=20)
    r.raise_for_status()
    rosters = r.json()
    ids = []
    for team in rosters:
        # Combine standard roster + IR + taxi (adjust to taste)
        for key in ("players", "reserve", "taxi"):
            vals = team.get(key) or []
            ids.extend(vals)
    # Deduplicate while preserving order
    seen = set()
    out = []
    for pid in ids:
        if pid is None:
            continue
        if pid not in seen:
            seen.add(pid)
            out.append(str(pid))
    return out

def chunks(iterable, size):
    it = iter(iterable)
    while True:
        batch = list(islice(it, size))
        if not batch:
            break
        yield batch

def build_payload(season: str, season_type: str, week: int, player_ids: list[str]) -> dict:
    # Use GraphQL variables so you never hardcode week or players
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
        "week": week,
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

def fetch_live_projections_for_league(league_id: str, max_ids_per_call: int = 200):
    state = get_sleeper_state()
    season = state["season"]
    season_type = state["season_type"]  # usually "regular" during the season, "post" for playoffs
    week = state["week"]

    player_ids = get_league_player_ids(league_id)

    # Some GraphQL backends have arg length limits—batch defensively.
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
    """Turn API rows into flat DataFrame with stats dict expanded."""
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

if __name__ == "__main__":
    res = fetch_live_projections_for_league(SLEEPER_LEAGUE_ID)

    # Flatten
    stats_df = to_flat_df(res.get("stats", []), "stat")
    proj_df  = to_flat_df(res.get("proj",  []), "proj")

    # Always write separate CSVs so you can peek at them if needed
    stats_path = f"outputs/sports/nfl/fantasy_stats_week{res['week']}.csv"
    proj_path  = f"outputs/sports/nfl/fantasy_proj_week{res['week']}.csv"
    stats_df.to_csv(stats_path, index=False)
    proj_df.to_csv(proj_path, index=False)

    # --- Diagnostics (helps you see what's wrong if merge fails) ---
    print(f"stats rows: {len(stats_df)} | cols: {list(stats_df.columns)[:12]}...")
    print(f"proj  rows: {len(proj_df)} | cols: {list(proj_df.columns)[:12]}...")
    # Uncomment to inspect a couple of rows:
    # print("stats sample:", stats_df.head(2).to_dict(orient="records"))
    # print("proj  sample:", proj_df.head(2).to_dict(orient="records"))

    # Try progressively smaller join key sets (most strict -> least strict)
    key_priority = [
        ["player_id","week","season","team","game_id","opponent"],
        ["player_id","week","season","team"],
        ["player_id","week","season"],
        ["player_id","week"],
        ["player_id"],
    ]

    merged = None
    used_keys = None
    if not stats_df.empty and not proj_df.empty:
        for keys in key_priority:
            if all(k in stats_df.columns for k in keys) and all(k in proj_df.columns for k in keys):
                try:
                    merged = pd.merge(
                        stats_df, proj_df,
                        on=keys, how="outer",
                        suffixes=("_stat", "_proj")
                    )
                    used_keys = keys
                    break
                except Exception as e:
                    print(f"merge on {keys} failed: {e}")

    if merged is None:
        # Fallback: stack with a source flag so nothing is lost
        combined = pd.concat(
            [stats_df.assign(source="stat"), proj_df.assign(source="proj")],
            ignore_index=True, sort=False
        )
        combined_path = f"nfl_stats_and_proj_week{res['week']}_stacked.csv"
        combined.to_csv(combined_path, index=False)
        print(
            "⚠️ Merge skipped (empty dfs or no common keys). "
            f"Wrote:\n- {stats_path}\n- {proj_path}\n- {combined_path}"
        )
    else:
        merged_path = f"nfl_stats_and_proj_week{res['week']}.csv"
        merged.to_csv(merged_path, index=False)
        print(
            f"✅ Wrote:\n- {stats_path}\n- {proj_path}\n- {merged_path} "
            f"(joined on {used_keys})"
        )