#!/usr/bin/env python3
"""
fetchPlayerHandedness.py
========================
One-time (+ periodic refresh) fetch of batter/pitcher handedness from MLB Stats API.
Builds a lookup: mlbam_id -> {bat_side, pitch_hand}

Output:
  outputs/sports/mlb/fantasy/playerData/player_handedness.csv
    columns: mlbam_id, player_name, bat_side (L/R/S), pitch_hand (L/R/S)

Run once to seed, then include in weekly refresh (roster changes, call-ups).
"""

import os
import requests
import pandas as pd
import time

BASE_URL = "https://statsapi.mlb.com/api/v1"
DATA_DIR = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/playerData")
OUT_PATH = os.path.join(DATA_DIR, "player_handedness.csv")
os.makedirs(DATA_DIR, exist_ok=True)


def get_active_rosters():
    """Get all active 40-man roster player IDs from all 30 teams."""
    teams_url = f"{BASE_URL}/teams?sportId=1&season=2026"
    r = requests.get(teams_url, timeout=15)
    r.raise_for_status()
    teams = r.json().get("teams", [])
    print(f"  Found {len(teams)} teams")

    player_ids = set()
    for team in teams:
        team_id = team["id"]
        roster_url = f"{BASE_URL}/teams/{team_id}/roster?rosterType=40Man"
        try:
            r = requests.get(roster_url, timeout=15)
            r.raise_for_status()
            roster = r.json().get("roster", [])
            for entry in roster:
                pid = entry.get("person", {}).get("id")
                if pid:
                    player_ids.add(pid)
        except Exception as e:
            print(f"  [warn] roster fetch failed for team {team_id}: {e}")
        time.sleep(0.05)  # polite rate limiting

    print(f"  Collected {len(player_ids)} unique player IDs from 40-man rosters")
    return player_ids


def fetch_handedness_batch(player_ids, batch_size=50):
    """
    Fetch handedness for a list of player IDs.
    MLB Stats API supports comma-separated person IDs.
    """
    rows = []
    ids = list(player_ids)
    total = len(ids)

    for i in range(0, total, batch_size):
        batch = ids[i:i + batch_size]
        id_str = ",".join(str(x) for x in batch)
        url = f"{BASE_URL}/people?personIds={id_str}&hydrate=currentTeam"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            people = r.json().get("people", [])
            for p in people:
                rows.append({
                    "mlbam_id":    p.get("id"),
                    "player_name": p.get("fullName", ""),
                    "bat_side":    p.get("batSide", {}).get("code", ""),   # L / R / S
                    "pitch_hand":  p.get("pitchHand", {}).get("code", ""), # L / R / S
                    "position":    p.get("primaryPosition", {}).get("abbreviation", ""),
                })
        except Exception as e:
            print(f"  [warn] batch {i//batch_size} failed: {e}")
        time.sleep(0.1)
        if (i // batch_size) % 10 == 0:
            print(f"  Progress: {min(i + batch_size, total)}/{total}")

    return rows


def main():
    # Check if we already have a file — if recent, just refresh additions
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH)
        print(f"  Existing handedness file: {len(existing)} players")
        existing_ids = set(existing["mlbam_id"].dropna().astype(int))
    else:
        existing     = pd.DataFrame()
        existing_ids = set()

    print("[fetchPlayerHandedness] fetching active rosters...")
    active_ids = get_active_rosters()

    new_ids = active_ids - existing_ids
    print(f"  {len(new_ids)} new players to fetch (skipping {len(existing_ids)} already known)")

    if not new_ids and not existing.empty:
        print("  Nothing new to fetch.")
        return

    fetch_ids = new_ids if not existing.empty else active_ids
    print(f"[fetchPlayerHandedness] fetching handedness for {len(fetch_ids)} players...")
    rows = fetch_handedness_batch(fetch_ids)

    new_df = pd.DataFrame(rows)
    if not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["mlbam_id"], keep="last")
    else:
        combined = new_df

    combined.to_csv(OUT_PATH, index=False)
    print(f"[fetchPlayerHandedness] wrote {len(combined)} players -> {OUT_PATH}")
    print(f"  bat_side distribution: {combined['bat_side'].value_counts().to_dict()}")
    print(f"  pitch_hand distribution: {combined['pitch_hand'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
