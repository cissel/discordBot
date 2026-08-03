#!/usr/bin/env python3
"""
fetchNflAdpAndRoster.py

Phase 3 data pull: current 2026 ADP + roster/team mapping for all fantasy-
relevant players (QB/RB/WR/TE), sourced from Sleeper (free, no key, same API
already used for league settings). Sleeper's GraphQL projections endpoint
carries `adp_dd_ppr` (overall ADP) and `pos_adp_dd_ppr` (positional ADP) plus
its own baseline point projections we can use as a cross-check against our
Phase 2 model - if they disagree wildly, investigate before trusting VORP.

Why Sleeper over FantasyPros: FantasyPros ADP page renders via JS (no plain
HTML table, and their JSON API returns 403 without partner auth). Sleeper's
GraphQL is public, unauthenticated, and already proven reliable in this repo
(sleeperProj.py uses the same endpoint for weekly projections).

Usage: venv/bin/python3 python/fetchNflAdpAndRoster.py
Output: outputs/sports/nfl/fantasy/adp_2026.csv (current snapshot - overwritten
        every run, this is what all downstream tools read)
        outputs/sports/nfl/fantasy/adp_history.csv (daily long-format log,
        one row per player per snapshot_date, appended to on every run.
        Re-running same-day replaces that day's rows instead of duplicating,
        so safe to run more than once per day.) Added 2026-08-01 so ADP
        drift can be tracked day-over-day through the preseason - the
        cached snapshot had gone stale (3 days old) and drifted noticeably
        from the live Sleeper draft-room ADP James was seeing.
"""
import json
import os
import time
from datetime import date

import pandas as pd
import requests

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
os.makedirs(OUT_DIR, exist_ok=True)
HISTORY_PATH = os.path.join(OUT_DIR, "adp_history.csv")


def append_to_history(df: pd.DataFrame, source: str = "sleeper"):
    """Append today's snapshot to the long-format history log, replacing any
    existing rows for today+source (idempotent re-runs, no duplicate days)."""
    today = date.today().isoformat()
    snap = df[["player_id", "full_name", "position", "team", "adp_overall", "adp_position"]].copy()
    snap["snapshot_date"] = today
    snap["source"] = source

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)
        hist = hist[~((hist["snapshot_date"] == today) & (hist["source"] == source))]
        hist = pd.concat([hist, snap], ignore_index=True)
    else:
        hist = snap
    hist.to_csv(HISTORY_PATH, index=False)
    print(f"History log updated: {HISTORY_PATH} ({len(snap)} rows for {today}/{source}, "
          f"{len(hist)} total rows)")

GRAPHQL_URL = "https://sleeper.com/graphql"
PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
RELEVANT_POSITIONS = {"QB", "RB", "WR", "TE"}
BATCH_SIZE = 200  # keep GraphQL payload reasonable


def fetch_player_pool():
    """Full Sleeper player dictionary, filtered to fantasy-relevant skill
    positions. Retired/inactive players get filtered out downstream by ADP
    presence (no current ADP = not draft-relevant).

    NOTE: does NOT require p.get("team") to be set. Discovered 2026-07-29:
    Sleeper's `team` field is null for some rostered, active, real starters
    at the time of this pull (confirmed: Stefon Diggs, Tyreek Hill both
    showed team=None despite being active NFL players) - likely a roster
    sync lag on Sleeper's side around final cuts/trades. Requiring team was
    silently dropping real ADP-relevant players before they ever got an ADP
    lookup. Position-only filtering pulls in ~4x more candidates (~4000 vs
    ~970), but the ADP-presence dropna() downstream is the real relevance
    filter either way, so this is a size/latency tradeoff only, not a
    quality one - worth the extra ~10s per run to stop silently losing
    real players."""
    print("Fetching Sleeper player pool (~14MB, one-time daily-cacheable call)...")
    r = requests.get(PLAYERS_URL, timeout=60)
    r.raise_for_status()
    players = r.json()
    pool = {
        pid: p for pid, p in players.items()
        if p.get("position") in RELEVANT_POSITIONS
    }
    print(f"  -> {len(pool)} QB/RB/WR/TE players (team filter removed, see docstring)")
    return pool


def build_query(player_ids):
    pid_list = ",".join(f'"{pid}"' for pid in player_ids)
    query = f"""
    query get_player_score_and_projections_batch {{
      nfl__regular__2026__1__proj: stats_for_players_in_week(
        sport: "nfl", season: "2026", category: "proj", season_type: "regular",
        week: 1, player_ids: [{pid_list}]
      ) {{
        player_id
        team
        stats
      }}
    }}
    """
    return {
        "operationName": "get_player_score_and_projections_batch",
        "variables": {},
        "query": " ".join(line.strip() for line in query.splitlines()),
    }


def fetch_adp_batch(player_ids):
    payload = build_query(player_ids)
    r = requests.post(GRAPHQL_URL, json=payload, headers={"content-type": "application/json"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        print("GraphQL errors:", json.dumps(data["errors"])[:500])
        return []
    records = data.get("data", {}).get("nfl__regular__2026__1__proj") or []
    rows = []
    for rec in records:
        s = rec.get("stats") or {}
        rows.append({
            "player_id": rec.get("player_id"),
            "team": rec.get("team"),
            "adp_overall": s.get("adp_dd_ppr"),
            "adp_position": s.get("pos_adp_dd_ppr"),
            "sleeper_proj_pts_week1": s.get("pts_ppr"),
        })
    return rows


def main():
    pool = fetch_player_pool()
    player_ids = list(pool.keys())

    all_rows = []
    for i in range(0, len(player_ids), BATCH_SIZE):
        batch = player_ids[i:i + BATCH_SIZE]
        rows = fetch_adp_batch(batch)
        all_rows.extend(rows)
        print(f"  Batch {i // BATCH_SIZE + 1}: {len(rows)} rows returned")
        time.sleep(0.5)

    df = pd.DataFrame(all_rows)
    df["full_name"] = df["player_id"].map(lambda pid: pool.get(pid, {}).get("full_name"))
    df["position"] = df["player_id"].map(lambda pid: pool.get(pid, {}).get("position"))
    df["gsis_id"] = df["player_id"].map(lambda pid: pool.get(pid, {}).get("gsis_id"))

    # Filter to players with a real overall ADP - undrafted/retired players show NaN here
    before = len(df)
    df = df.dropna(subset=["adp_overall"])
    print(f"\nDropped {before - len(df)} players with no ADP (retired/undrafted/practice squad)")

    df = df.sort_values("adp_overall")
    out_path = os.path.join(OUT_DIR, "adp_2026.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    print(f"Rows: {len(df)}")
    print(f"\nTop 10 overall ADP:")
    print(df.head(10)[["full_name", "position", "team", "adp_overall", "adp_position"]].to_string(index=False))

    append_to_history(df, source="sleeper")


if __name__ == "__main__":
    main()
