#!/usr/bin/env python3
"""
nflDraftLive.py

Phase 4, step 2: live draft-day state tracker. Sits on top of
draft_board_2026.csv (built by buildNflDraftBoard.py) and maintains a simple
JSON file of who's been drafted, so /nfl draftboard can always show "best
remaining" during the actual live draft instead of a static pre-draft list.

State file: outputs/sports/nfl/fantasy/draft_state.json
  {"drafted": {"Full Player Name": "drafted_by (optional)", ...}}

Player name matching is case-insensitive exact match against full_name in
draft_board_2026.csv. Not fuzzy on purpose - avoids silently marking the
wrong player drafted during a live, time-pressured draft. Callers should use
autocomplete (wired in commands.py) rather than free-typing names.

Can be run standalone for CLI testing, but is primarily imported by
commands.py for the /nfl draftboard, draft, undraft, resetdraft subcommands.
"""
import json
import os

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
BOARD_PATH = os.path.join(OUT_DIR, "draft_board_2026.csv")
STATE_PATH = os.path.join(OUT_DIR, "draft_state.json")


def load_board() -> pd.DataFrame:
    if not os.path.exists(BOARD_PATH):
        raise FileNotFoundError(
            f"{BOARD_PATH} not found - run buildNflDraftBoard.py first."
        )
    return pd.read_csv(BOARD_PATH)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"drafted": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def all_player_names() -> list[str]:
    """Full player list for autocomplete (all positions, all ADP ranges)."""
    return load_board()["full_name"].dropna().tolist()


def find_player_row(board: pd.DataFrame, name: str):
    """Case-insensitive exact match. Returns the matching row or None."""
    matches = board[board["full_name"].str.lower() == name.lower().strip()]
    if matches.empty:
        return None
    return matches.iloc[0]


def mark_drafted(name: str, drafted_by: str = "") -> tuple[bool, str]:
    board = load_board()
    row = find_player_row(board, name)
    if row is None:
        return False, f"'{name}' not found on the draft board."
    state = load_state()
    canonical = row["full_name"]
    if canonical in state["drafted"]:
        return False, f"{canonical} was already marked drafted."
    state["drafted"][canonical] = drafted_by
    save_state(state)
    return True, f"Marked drafted: {canonical} ({row['position']})" + (
        f" -> {drafted_by}" if drafted_by else ""
    )


def undo_draft(name: str) -> tuple[bool, str]:
    board = load_board()
    row = find_player_row(board, name)
    canonical = row["full_name"] if row is not None else name
    state = load_state()
    if canonical not in state["drafted"]:
        return False, f"{canonical} wasn't marked drafted."
    del state["drafted"][canonical]
    save_state(state)
    return True, f"Un-drafted: {canonical}"


def reset_draft() -> None:
    save_state({"drafted": {}})


def best_available(position: str = None, limit: int = 12) -> pd.DataFrame:
    """Returns top `limit` remaining players by VORP, optionally filtered
    to one position. Excludes anyone in draft_state.json."""
    board = load_board()
    state = load_state()
    drafted_names = set(state["drafted"].keys())
    available = board[~board["full_name"].isin(drafted_names)].copy()
    if position:
        available = available[available["position"] == position.upper()]
    return available.sort_values("vorp", ascending=False).head(limit)


def draft_progress() -> dict:
    board = load_board()
    state = load_state()
    n_drafted = len(state["drafted"])
    by_pos = board[board["full_name"].isin(state["drafted"].keys())]["position"].value_counts().to_dict()
    return {"n_drafted": n_drafted, "by_position": by_pos, "total_board": len(board)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: nflDraftLive.py [best <pos>|draft <name>|undraft <name>|reset|progress]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "best":
        pos = sys.argv[2] if len(sys.argv) > 2 else None
        print(best_available(pos).to_string(index=False))
    elif cmd == "draft":
        ok, msg = mark_drafted(" ".join(sys.argv[2:]))
        print(msg)
    elif cmd == "undraft":
        ok, msg = undo_draft(" ".join(sys.argv[2:]))
        print(msg)
    elif cmd == "reset":
        reset_draft()
        print("Draft state reset.")
    elif cmd == "progress":
        print(draft_progress())
