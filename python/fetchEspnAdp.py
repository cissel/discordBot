#!/usr/bin/env python3
"""
fetchEspnAdp.py

Phase 3 extension: pull ESPN's current 2026 ADP as a second platform source,
alongside Sleeper (fetchNflAdpAndRoster.py). Public, unauthenticated
lm-api-reads.fantasy.espn.com endpoint - same one ESPN's own fantasy site
uses client-side, no API key needed (confirmed 2026-08-01).

Used together with the Sleeper snapshot to measure cross-platform ADP
dispersion (buildAdpDispersion.py) - James wants to see which players
Sleeper and ESPN disagree on most, as a market-inefficiency signal, and
which platform's real drafters are ahead/behind on emerging players.

Position IDs (ESPN's defaultPositionId, confirmed via live pull):
  1=QB, 2=RB, 3=WR, 4=TE, 5=K, 16=D/ST. We only keep QB/RB/WR/TE to match
  the rest of the Room 40 fantasy pipeline (RELEVANT_POSITIONS elsewhere).

NOTE: ESPN's player pool has no shared ID with Sleeper's player_id space -
downstream merging (buildAdpDispersion.py) has to join on normalized name,
same suffix/accent-stripping approach as buildNfl2026Projections.py's
norm_name() (Sleeper <-> nflverse join already solves the identical
problem there).

Usage: venv/bin/python3 python/fetchEspnAdp.py
Output: outputs/sports/nfl/fantasy/espn_adp_2026.csv (current snapshot)
        outputs/sports/nfl/fantasy/adp_history.csv (appends source="espn"
        rows here too - same long-format log fetchNflAdpAndRoster.py writes
        to, joined by snapshot_date+source downstream)
"""
import json
import os
from datetime import date

import pandas as pd
import requests

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
os.makedirs(OUT_DIR, exist_ok=True)
HISTORY_PATH = os.path.join(OUT_DIR, "adp_history.csv")

ESPN_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/players"
POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
LIMIT = 800  # comfortably above the ~2600 players-with-adp seen in testing across all positions


def fetch_espn_adp() -> pd.DataFrame:
    params = {"scoringPeriodId": 0, "view": "kona_player_info"}
    filt = {
        "players": {
            "limit": LIMIT,
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "PPR"},
        }
    }
    headers = {"x-fantasy-filter": json.dumps(filt), "User-Agent": "Mozilla/5.0"}
    r = requests.get(ESPN_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    players = r.json()

    rows = []
    for p in players:
        pos_id = p.get("defaultPositionId")
        if pos_id not in POSITION_MAP:
            continue
        own = p.get("ownership") or {}
        adp = own.get("averageDraftPosition")
        if not adp:
            continue
        rows.append({
            "espn_id": p.get("id"),
            "full_name": p.get("fullName"),
            "position": POSITION_MAP[pos_id],
            "adp_overall": adp,
            "percent_owned": own.get("percentOwned"),
            "percent_started": own.get("percentStarted"),
        })
    return pd.DataFrame(rows)


def append_to_history(df: pd.DataFrame, source: str = "espn"):
    """Append today's snapshot to the shared long-format history log
    (same file fetchNflAdpAndRoster.py writes to), replacing any existing
    rows for today+source so re-runs are idempotent."""
    today = date.today().isoformat()
    snap = df[["full_name", "position", "adp_overall"]].copy()
    snap["player_id"] = pd.array([None] * len(snap), dtype="object")  # no shared ID space with Sleeper - join on name downstream
    snap["team"] = pd.array([None] * len(snap), dtype="object")
    snap["adp_position"] = pd.array([None] * len(snap), dtype="object")
    snap["snapshot_date"] = today
    snap["source"] = source
    snap = snap[["player_id", "full_name", "position", "team", "adp_overall", "adp_position", "snapshot_date", "source"]]

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)
        hist = hist[~((hist["snapshot_date"] == today) & (hist["source"] == source))]
        if hist.empty:
            hist = snap
        else:
            hist = pd.concat([hist, snap], ignore_index=True)
    else:
        hist = snap
    hist.to_csv(HISTORY_PATH, index=False)
    print(f"History log updated: {HISTORY_PATH} ({len(snap)} rows for {today}/{source}, "
          f"{len(hist)} total rows)")


def main():
    df = fetch_espn_adp()
    if df.empty:
        raise SystemExit("ESPN returned 0 rows with ADP - endpoint may have changed, investigate before trusting downstream.")
    df = df.sort_values("adp_overall")
    out_path = os.path.join(OUT_DIR, "espn_adp_2026.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    print(f"Rows: {len(df)}")
    print("\nTop 10 ESPN ADP:")
    print(df.head(10)[["full_name", "position", "adp_overall"]].to_string(index=False))

    append_to_history(df, source="espn")


if __name__ == "__main__":
    main()
