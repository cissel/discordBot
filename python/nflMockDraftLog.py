#!/usr/bin/env python3
"""
nflMockDraftLog.py

Phase 4, step 5: pull + grade a completed Sleeper mock draft, and persist it
to a running log so multiple mock drafts can be compared side by side.

Grading uses the same VORP board as everything else (vorp_2026.csv) so mock
drafts are graded on the same scale as the live draftboard/cheat sheet - no
separate scoring system to keep in sync.

GRADE COMPONENTS per drafted team:
  - starting_lineup_vorp: optimal starting lineup VORP (same method as
    buildNflDraftStrategySim.py - bench doesn't count)
  - mean_value_gap: average (adp_rank - vorp_rank) across the team's picks -
    positive = you got good value relative to market ADP
  - pick_grades: per-pick value_gap, so you can see WHICH picks were good/bad,
    not just the final score

Log storage: outputs/sports/nfl/fantasy/mock_draft_log.csv (one row per
mock draft, so /nfl mockdrafts can list + compare all of them over time).

Usage (CLI):
  venv/bin/python3 python/nflMockDraftLog.py grade <sleeper_draft_id> [my_slot]
  venv/bin/python3 python/nflMockDraftLog.py list
  venv/bin/python3 python/nflMockDraftLog.py show <log_row_id>
"""
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
VORP_PATH = os.path.join(OUT_DIR, "vorp_2026.csv")
LOG_PATH = os.path.join(OUT_DIR, "mock_draft_log.csv")

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
SLEEPER_DRAFT_URL = "https://api.sleeper.app/v1/draft/{draft_id}"
SLEEPER_PICKS_URL = "https://api.sleeper.app/v1/draft/{draft_id}/picks"


class MockDraftError(Exception):
    pass


def fetch_sleeper_draft(draft_id: str) -> dict:
    r = requests.get(SLEEPER_DRAFT_URL.format(draft_id=draft_id), timeout=20)
    r.raise_for_status()
    meta = r.json()
    if meta is None:
        raise MockDraftError(f"No Sleeper draft found for ID '{draft_id}' - check the ID/link.")
    return meta


def fetch_sleeper_picks(draft_id: str) -> list:
    r = requests.get(SLEEPER_PICKS_URL.format(draft_id=draft_id), timeout=20)
    r.raise_for_status()
    picks = r.json()
    if not isinstance(picks, list):
        raise MockDraftError(
            f"Sleeper returned an unexpected response shape for draft '{draft_id}' "
            "picks (expected a list) - the draft ID is likely malformed (e.g. a "
            "leftover '?query=string' from copy-pasting a full URL). Try passing "
            "just the numeric draft ID."
        )
    if not picks:
        raise MockDraftError(
            f"Draft '{draft_id}' has no picks yet - has the mock draft finished?"
        )
    return picks


def load_vorp_board() -> pd.DataFrame:
    if not os.path.exists(VORP_PATH):
        raise MockDraftError(f"{VORP_PATH} not found - run buildNflVorp.py first.")
    return pd.read_csv(VORP_PATH)


def load_id_bridge() -> dict:
    """Sleeper player_id -> full_name, sourced from adp_2026.csv (built by
    fetchNflAdpAndRoster.py from the same Sleeper player pool draft picks
    reference). Matching on player_id is exact and robust vs. name-string
    matching (handles Jr./III suffixes, accented characters, etc.) - use
    this as the PRIMARY match path; fall back to name matching only for
    picks whose player_id isn't in our board (e.g. exact rookie name gaps)."""
    adp_path = os.path.join(OUT_DIR, "adp_2026.csv")
    if not os.path.exists(adp_path):
        return {}
    adp_df = pd.read_csv(adp_path)
    return dict(zip(adp_df["player_id"].astype(str), adp_df["full_name"]))


def match_player(vorp_df: pd.DataFrame, sleeper_player_id: str, first: str, last: str, position: str):
    """Primary: exact Sleeper player_id -> full_name bridge (see
    load_id_bridge). Fallback: last name + position substring match against
    our board's full_name, for players our id bridge doesn't cover."""
    id_bridge = match_player._id_bridge
    if sleeper_player_id and str(sleeper_player_id) in id_bridge:
        full_name = id_bridge[str(sleeper_player_id)]
        exact = vorp_df[vorp_df["full_name"] == full_name]
        if not exact.empty:
            return exact.iloc[0]

    full = f"{first} {last}".strip()
    exact = vorp_df[vorp_df["full_name"].str.lower() == full.lower()]
    if not exact.empty:
        return exact.iloc[0]
    candidates = vorp_df[
        (vorp_df["position"] == position)
        & vorp_df["full_name"].str.lower().str.contains(last.lower(), regex=False)
    ]
    if not candidates.empty:
        return candidates.iloc[0]
    return None


match_player._id_bridge = {}  # populated by grade_draft() before use


def score_starting_lineup(team_rows: list) -> float:
    df = pd.DataFrame(team_rows)
    if df.empty:
        return 0.0
    total = 0.0
    starters_names = set()
    for pos, n in STARTERS.items():
        pos_df = df[df["position"] == pos].sort_values("vorp", ascending=False)
        top_n = pos_df.head(n)
        total += top_n["vorp"].sum()
        starters_names.update(top_n["full_name"].tolist())
    remaining = df[~df["full_name"].isin(starters_names) & df["position"].isin(FLEX_ELIGIBLE)]
    if not remaining.empty:
        total += remaining.sort_values("vorp", ascending=False).iloc[0]["vorp"]
    return total


def grade_draft(draft_id: str, my_slot: int = None, my_user_id: str = None) -> dict:
    """Grades every team in the draft, returns full result dict. If my_slot
    or my_user_id given, flags which team is 'yours' for the summary.

    Teams are grouped by `draft_slot` (1..N, the snake-draft position - e.g.
    "I have pick 10" = draft_slot 10), NOT `roster_id`. Confirmed 2026-07-30:
    Sleeper's `roster_id` is null on standalone mock drafts (no real league
    rosters exist yet) but IS populated - and randomly assigned, NOT equal to
    draft_slot - on real league drafts (e.g. slot 1 belonged to roster_id 2,
    slot 2 to roster_id 12 in Room 40's actual 2025 draft). Grouping by
    roster_id with a draft_slot fallback (the old behavior) worked by
    accident for mocks but would have silently graded the WRONG team if ever
    run against a real completed league draft where a user entered their
    pick-order slot number. draft_slot is always present and unambiguous -
    use it exclusively."""
    meta = fetch_sleeper_draft(draft_id)
    picks = fetch_sleeper_picks(draft_id)
    vorp_df = load_vorp_board()
    match_player._id_bridge = load_id_bridge()

    settings = meta.get("settings", {})
    n_teams = settings.get("teams") or len({p["draft_slot"] for p in picks})

    teams = {}         # draft_slot -> list of matched vorp rows
    unmatched = []      # picks we couldn't map to the board
    pick_grades = {}    # draft_slot -> list of per-pick dicts

    for p in picks:
        draft_slot = p.get("draft_slot")
        meta_p = p.get("metadata", {}) or {}
        first, last = meta_p.get("first_name", ""), meta_p.get("last_name", "")
        position = meta_p.get("position", p.get("position", ""))
        sleeper_player_id = p.get("player_id")
        row = match_player(vorp_df, sleeper_player_id, first, last, position)
        teams.setdefault(draft_slot, [])
        pick_grades.setdefault(draft_slot, [])
        if row is None:
            unmatched.append(f"{first} {last} ({position})")
            continue
        teams[draft_slot].append(row.to_dict())
        pick_grades[draft_slot].append({
            "pick_no": p.get("pick_no"),
            "round": p.get("round"),
            "full_name": row["full_name"],
            "position": row["position"],
            "adp_overall": row["adp_overall"],
            "vorp": row["vorp"],
            "value_gap": row["value_gap"],
        })

    team_scores = {}
    for draft_slot, rows in teams.items():
        lineup_vorp = score_starting_lineup(rows)
        gaps = [r["value_gap"] for r in pick_grades[draft_slot] if pd.notna(r["value_gap"])]
        team_scores[draft_slot] = {
            "starting_lineup_vorp": round(lineup_vorp, 1),
            "mean_value_gap": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "n_picks": len(rows),
            "picks": pick_grades[draft_slot],
        }

    ranked = sorted(team_scores.items(), key=lambda kv: kv[1]["starting_lineup_vorp"], reverse=True)

    result = {
        "draft_id": draft_id,
        "graded_at": datetime.now(timezone.utc).isoformat(),
        "n_teams": n_teams,
        "n_unmatched": len(unmatched),
        "unmatched_sample": unmatched[:10],
        "team_scores": team_scores,
        "ranking": [{"draft_slot": slot, **scores_without_picks(s)} for slot, s in ranked],
        "my_draft_slot": my_slot,
    }
    return result


def scores_without_picks(score_dict: dict) -> dict:
    return {k: v for k, v in score_dict.items() if k != "picks"}


def append_to_log(result: dict, label: str = "") -> int:
    """Appends a summary row to mock_draft_log.csv. Returns the new row's id."""
    my_slot = result.get("my_draft_slot")
    my_score = None
    my_rank = None
    if my_slot is not None and my_slot in result["team_scores"]:
        my_score = result["team_scores"][my_slot]["starting_lineup_vorp"]
        my_rank = next((i + 1 for i, r in enumerate(result["ranking"])
                         if r["draft_slot"] == my_slot), None)

    row = {
        "draft_id": result["draft_id"],
        "graded_at": result["graded_at"],
        "label": label,
        "n_teams": result["n_teams"],
        "my_draft_slot": my_slot,
        "my_starting_lineup_vorp": my_score,
        "my_rank_out_of_field": my_rank,
        "field_best_vorp": result["ranking"][0]["starting_lineup_vorp"] if result["ranking"] else None,

        "field_mean_vorp": round(
            sum(r["starting_lineup_vorp"] for r in result["ranking"]) / len(result["ranking"]), 1
        ) if result["ranking"] else None,
        "n_unmatched": result["n_unmatched"],
    }

    if os.path.exists(LOG_PATH):
        log_df = pd.read_csv(LOG_PATH)
        row["id"] = int(log_df["id"].max()) + 1 if len(log_df) else 1
        log_df = pd.concat([log_df, pd.DataFrame([row])], ignore_index=True)
    else:
        row["id"] = 1
        log_df = pd.DataFrame([row])

    log_df.to_csv(LOG_PATH, index=False)

    # also stash the full per-pick detail for this row so /nfl mockdraft
    # show <id> can drill in later without re-hitting the Sleeper API
    detail_path = os.path.join(OUT_DIR, "mock_draft_details", f"{row['id']}.json")
    os.makedirs(os.path.dirname(detail_path), exist_ok=True)
    with open(detail_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    return row["id"]


def list_log() -> pd.DataFrame:
    if not os.path.exists(LOG_PATH):
        return pd.DataFrame()
    return pd.read_csv(LOG_PATH).sort_values("graded_at", ascending=False)


def load_detail(row_id: int) -> dict:
    detail_path = os.path.join(OUT_DIR, "mock_draft_details", f"{row_id}.json")
    if not os.path.exists(detail_path):
        raise MockDraftError(f"No stored detail for log id {row_id}.")
    with open(detail_path) as f:
        return json.load(f)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: nflMockDraftLog.py [grade <draft_id> [my_slot]|list|show <id>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "grade":
        draft_id = sys.argv[2]
        my_slot = int(sys.argv[3]) if len(sys.argv) > 3 else None
        result = grade_draft(draft_id, my_slot=my_slot)
        row_id = append_to_log(result, label=sys.argv[4] if len(sys.argv) > 4 else "")
        print(f"Logged as id {row_id}")
        print(json.dumps(result["ranking"], indent=2))
        if result["n_unmatched"]:
            print(f"\nWARNING: {result['n_unmatched']} picks unmatched to VORP board: "
                  f"{result['unmatched_sample']}")
    elif cmd == "list":
        print(list_log().to_string(index=False))
    elif cmd == "show":
        print(json.dumps(load_detail(int(sys.argv[2])), indent=2))
