#!/usr/bin/env python3
"""
worldSilliesCumFP.py
Fetches per-day fantasy points for every player on a World Sillies ESPN team
and writes long-format CSVs for the cumulative fantasy points R plot.

Usage:
  python worldSilliesCumFP.py [--team <id|name>] [--force]

  --team   team id (1-8) or partial team name (case-insensitive). Defaults to 4 (dock ellis fan club).
  --force  bypass cache and re-fetch everything

Outputs (per-team, keyed by team id):
  ~/discordBot/outputs/sports/mlb/fantasy/cumfp_t{id}.csv
  ~/discordBot/outputs/sports/mlb/fantasy/cumfp_t{id}_meta.csv
"""

import csv
import json
import datetime
import os
import sys
import time
from pathlib import Path

import requests

LEAGUE_ID    = 1858112591
YEAR         = 2026
DEFAULT_TEAM = 4          # dock ellis fan club
SEASON_START = datetime.date(2026, 3, 27)   # Opening Day 2026

# All teams: id -> name (for fuzzy match on --team arg)
TEAMS: dict[int, str] = {
    1: "Chandler Simpson Worshipper",
    2: "Zach's Baseball Classic",
    3: "2 balls 1 bat",
    4: "dock ellis fan club",
    5: "Jose Caballero",
    6: "JungHooLee is My Father",
    7: "Nolan Ryan's Right Hook",
    8: "UNCLE CUCKUS",
}

BASE = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
    f"/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)"}

OUT_DIR  = Path("~/discordBot/outputs/sports/mlb/fantasy").expanduser()
OUT_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 7200  # 2 hours


def out_paths(team_id: int) -> tuple[Path, Path]:
    """Return (csv_path, meta_path) for a given team id."""
    return (
        OUT_DIR / f"cumfp_t{team_id}.csv",
        OUT_DIR / f"cumfp_t{team_id}_meta.csv",
    )


def resolve_team(arg: str) -> tuple[int, str]:
    """
    Resolve a --team argument (numeric id or partial name) to (team_id, team_name).
    Raises SystemExit on no match.
    """
    # Numeric id
    try:
        tid = int(arg)
        if tid in TEAMS:
            return tid, TEAMS[tid]
        sys.exit(f"[cumfp] unknown team id {tid}. Valid ids: {list(TEAMS)}")
    except ValueError:
        pass

    # Fuzzy name match
    arg_lower = arg.lower()
    matches = [(tid, name) for tid, name in TEAMS.items() if arg_lower in name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sys.exit(f"[cumfp] ambiguous team name '{arg}': {[m[1] for m in matches]}")
    sys.exit(f"[cumfp] no team matching '{arg}'. Options:\n" +
             "\n".join(f"  {tid}: {name}" for tid, name in TEAMS.items()))

# ESPN defaultPositionId -> position label
ESPN_POS = {
    1:  "SP",
    2:  "C",
    3:  "1B",
    4:  "2B",
    5:  "3B",
    6:  "SS",
    7:  "LF",
    8:  "CF",
    9:  "RF",
    10: "DH",
    11: "RP",
    12: "UTIL",
    13: "P",
}

# League scoring formula: statId -> points per unit
# Batting: H=1, HR=1, R=1, RBI=1, SB=1, CS=-1
# Pitching: K=1, ER=-1, BB=-1, HRA=-2, IP=1, W=2, L=-2, SV=5, QS=2
SCORING_FORMULA: dict[int, float] = {
    8: 1.0, 10: 1.0, 20: 1.0, 21: 1.0, 23: 1.0, 27: -1.0,   # batting
    34: 1.0, 37: -1.0, 39: -1.0, 45: -2.0, 48: 1.0,           # pitching
    53: 2.0, 54: -2.0, 57: 5.0, 60: 2.0,                       # pitching cont.
}
BATTING_STAT_IDS  = {8, 10, 20, 21, 23, 27}
PITCHING_STAT_IDS = {34, 37, 39, 45, 48, 53, 54, 57, 60}

# Players eligible for pitcher slots who also bat get TWO rows in the output:
# one with batting-only points, one with pitching-only points.
# ESPN slot IDs that indicate pitcher eligibility
PITCHER_ELIGIBLE_SLOTS = {13, 14, 15}   # SP=13, RP=14/15

# Minimum expected coverage fraction before triggering kona fallback
COVERAGE_THRESHOLD = 0.60
# If a player's earliest data point is beyond this period, force kona fallback
# for the gap regardless of overall coverage %
EARLY_GAP_THRESHOLD = 5


def period_to_date(p: int) -> datetime.date:
    return SEASON_START + datetime.timedelta(days=p - 1)


def espn_get(params: dict, extra_headers: dict | None = None) -> dict:
    hdrs = {**HEADERS, **(extra_headers or {})}
    r = requests.get(BASE, params=params, headers=hdrs, timeout=20)
    r.raise_for_status()
    return r.json()


def get_latest_period() -> int:
    data = espn_get({"view": "mSettings"})
    return int(data.get("status", {}).get("latestScoringPeriod", 92))


def get_current_roster(team_id: int) -> list[dict]:
    """
    Return list of dicts with player info for the current dock ellis fan club roster.
    Keys: player_id, player_name, position, pos_id, eligible_slots,
          acq_date, acq_scoring_period, is_draft
    """
    data = espn_get({"view": "mRoster"})
    my_team = next((t for t in data.get("teams", []) if t["id"] == team_id), None)
    if not my_team:
        return []

    entries = my_team.get("roster", {}).get("entries", [])
    roster = []
    for e in entries:
        player    = e.get("playerPoolEntry", {}).get("player", {})
        pid       = e.get("playerId")
        name      = player.get("fullName", "Unknown")
        pos_id    = player.get("defaultPositionId", 0)
        pos       = ESPN_POS.get(pos_id, f"id={pos_id}")
        eligible  = player.get("eligibleSlots", [])
        acq_ms    = e.get("acquisitionDate", 0)
        acq_type  = e.get("acquisitionType", "")

        if acq_ms:
            acq_dt = datetime.datetime.fromtimestamp(acq_ms / 1000).date()
            sp     = max(1, (acq_dt - SEASON_START).days + 1)
        else:
            acq_dt = None
            sp     = 1

        is_draft = (acq_type == "DRAFT") or (acq_dt is not None and acq_dt < SEASON_START)
        eligible_set = set(eligible)
        is_two_way = (pos_id not in {1, 11}) and bool(eligible_set & PITCHER_ELIGIBLE_SLOTS)

        roster.append({
            "player_id":          pid,
            "player_name":        name,
            "position":           pos,
            "pos_id":             pos_id,
            "eligible_slots":     ",".join(str(s) for s in eligible),
            "is_two_way":         is_two_way,
            # Write empty string for draft picks so R reads acq_date as NA
            "acq_date":           "" if is_draft else (acq_dt.isoformat() if acq_dt else ""),
            "acq_scoring_period": sp if not is_draft else "",
            "is_draft":           is_draft,
        })
    return roster


def extract_daily_stats(
    entries: list[dict],
    two_way_names: set[str] | None = None,
) -> dict[tuple, float]:
    """
    Extract (player_name, scoring_period) -> daily_pts from roster entries.

    For two-way players (e.g. Ohtani), ESPN populates appliedStats with only the
    relevant stat IDs per day (batting IDs on batting days, pitching IDs on pitching days).
    We emit TWO keys:
      (name,          sp) -> batting points  (sum of BATTING_STAT_IDS in appliedStats)
      (name + " ★",   sp) -> pitching points (sum of PITCHING_STAT_IDS in appliedStats)
    On days where appliedStats is empty (no game) both are 0.
    """
    two_way_names = two_way_names or set()
    out: dict[tuple, float] = {}

    for entry in entries:
        player = entry.get("playerPoolEntry", {}).get("player", {})
        name   = player.get("fullName", "Unknown")
        for s in player.get("stats", []):
            if s.get("statSplitTypeId") != 5 or s.get("statSourceId") != 0:
                continue
            sp  = s.get("scoringPeriodId")
            if not sp or sp <= 0:
                continue

            if name in two_way_names:
                app = s.get("appliedStats", {})
                raw = s.get("stats", {})
                # Batting pts: sum appliedStats values for batting IDs
                # (appliedStats already stores point-weighted values)
                bat_pts = sum(
                    v for k, v in app.items() if int(k) in BATTING_STAT_IDS
                )
                # Pitching pts: compute from RAW stats × scoring formula.
                # We deliberately ignore appliedStats for pitching because ESPN
                # only populates it when the owner started Ohtani in a pitcher slot.
                # Raw stats reflect his actual game performance every start.
                pit_pts = sum(
                    raw.get(str(sid), 0.0) * pts
                    for sid, pts in SCORING_FORMULA.items()
                    if sid in PITCHING_STAT_IDS
                )
                out[(name,             sp)] = round(bat_pts, 2)
                out[(name + " \u2605",  sp)] = round(pit_pts, 2)
            else:
                out[(name, sp)] = s.get("appliedTotal", 0.0)

    return out


def phase1_scan_all_teams(latest: int, two_way_names: set[str]) -> dict[tuple, float]:
    """
    Phase 1: Scan ALL 8 team rosters at each period.
    Stepping by 2 (each response gives periods N and N-1).
    """
    all_data: dict[tuple, float] = {}
    periods = sorted(set(range(2, latest + 1, 2)) | {latest})

    for i, sp in enumerate(periods, 1):
        try:
            data    = espn_get({"view": "mRoster", "scoringPeriodId": sp})
            entries = []
            for team in data.get("teams", []):
                entries.extend(team.get("roster", {}).get("entries", []))
            daily = extract_daily_stats(entries, two_way_names)
            all_data.update(daily)
            print(f"  p1 [{i}/{len(periods)}] sp={sp}: {len(daily)} player-days", flush=True)
        except Exception as e:
            print(f"  [WARN] phase1 sp={sp}: {e}", file=sys.stderr)
        time.sleep(0.05)

    return all_data


def phase2_kona_fallback(
    current_roster: list[dict],
    all_data: dict[tuple, float],
    latest: int,
) -> dict[tuple, float]:
    """
    Phase 2: Fill gaps for FA pickups via kona_player_info.
    Triggers for a player if either:
      a) overall coverage < COVERAGE_THRESHOLD, OR
      b) their earliest data point is beyond EARLY_GAP_THRESHOLD
         (meaning they were a FA for a chunk of the early season)
    Fetches only the periods actually missing for each player.
    """
    extra: dict[tuple, float] = {}
    expected_periods = set(range(2, latest + 1, 2)) | {latest}
    all_periods      = set(range(2, latest + 1))

    for p in current_roster:
        name = p["player_name"]
        pid  = p["player_id"]

        # Collect periods we already have for this player (base name only, not ★ variant)
        have = {sp for (n, sp) in all_data if n == name}
        coverage    = len(have) / len(expected_periods) if expected_periods else 1.0
        first_have  = min(have) if have else latest + 1
        has_early_gap = first_have > EARLY_GAP_THRESHOLD

        if coverage >= COVERAGE_THRESHOLD and not has_early_gap:
            continue

        missing = sorted(all_periods - have)
        if not missing:
            continue

        reason = f"coverage={coverage:.0%}" if coverage < COVERAGE_THRESHOLD else f"early gap (first={first_have})"
        print(f"  p2 fallback: {name} ({reason}) - fetching {len(missing)} missing periods",
              flush=True)

        filter_val = json.dumps({
            "players": {
                "filterIds": {"value": [pid]},
                "filterStatsForSourceIds": {"value": [0]},
                "filterStatsForSplitTypeIds": {"value": [5]},
                "limit": 1,
                "offset": 0,
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        })

        fetched = 0
        for sp in missing:
            try:
                data = espn_get(
                    {"view": "kona_player_info", "scoringPeriodId": sp},
                    extra_headers={"x-fantasy-filter": filter_val},
                )
                for player_entry in data.get("players", []):
                    player_obj = player_entry.get("player", {})
                    for s in player_obj.get("stats", []):
                        if s.get("statSplitTypeId") == 5 and s.get("statSourceId") == 0:
                            s_sp  = s.get("scoringPeriodId")
                            s_pts = s.get("appliedTotal", 0.0)
                            if s_sp and s_sp > 0:
                                # kona returns appliedTotal (combined) - fine for non-two-way
                                extra[(name, s_sp)] = s_pts
                                fetched += 1
            except Exception as e:
                print(f"    [WARN] kona sp={sp}: {e}", file=sys.stderr)
            time.sleep(0.05)

        print(f"    -> filled {fetched} period entries for {name}", flush=True)

    return extra


def write_csv(data: dict[tuple, float], current_names: set[str], out_csv: Path) -> int:
    # Allow both base names AND their ★ variants (two-way pitcher rows)
    allowed = current_names | {n + " \u2605" for n in current_names}
    rows = []
    for (name, sp), pts in data.items():
        if name not in allowed:
            continue
        d = period_to_date(sp)
        rows.append({
            "player_name":    name,
            "scoring_period": sp,
            "date":           d.isoformat(),
            "daily_pts":      round(pts, 2),
        })
    rows.sort(key=lambda r: (r["player_name"], r["scoring_period"]))

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player_name", "scoring_period", "date", "daily_pts"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_meta(current_roster: list[dict], meta_csv: Path) -> None:
    with open(meta_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "player_name", "position", "pos_id", "eligible_slots", "is_two_way",
            "acq_date", "acq_scoring_period", "is_draft",
        ])
        w.writeheader()
        for p in current_roster:
            w.writerow({
                "player_name":        p["player_name"],
                "position":           p["position"],
                "pos_id":             p["pos_id"],
                "eligible_slots":     p["eligible_slots"],
                "is_two_way":         p["is_two_way"],
                "acq_date":           p["acq_date"],
                "acq_scoring_period": p["acq_scoring_period"],
                "is_draft":           p["is_draft"],
            })


def main():
    args  = sys.argv[1:]
    force = "--force" in args

    # Resolve --team argument
    team_id   = DEFAULT_TEAM
    team_name = TEAMS[DEFAULT_TEAM]
    if "--team" in args:
        idx = args.index("--team")
        if idx + 1 >= len(args):
            sys.exit("[cumfp] --team requires a value")
        team_id, team_name = resolve_team(args[idx + 1])

    out_csv, meta_csv = out_paths(team_id)
    print(f"[cumfp] team: {team_name} (id={team_id})", flush=True)

    if not force and out_csv.exists():
        age = time.time() - out_csv.stat().st_mtime
        if age < CACHE_TTL:
            print(f"[cumfp] cache fresh ({age / 3600:.1f}h old) - skipping fetch")
            return

    print("[cumfp] fetching current roster...", flush=True)
    current_roster = get_current_roster(team_id)
    current_names  = {p["player_name"] for p in current_roster}
    two_way_names  = {p["player_name"] for p in current_roster if p["is_two_way"]}
    if two_way_names:
        print(f"[cumfp] two-way players: {', '.join(sorted(two_way_names))}", flush=True)
    print(f"[cumfp] {len(current_roster)} players on roster", flush=True)

    print("[cumfp] fetching latest scoring period...", flush=True)
    latest = get_latest_period()
    print(f"[cumfp] latest scoring period = {latest}", flush=True)

    print(f"[cumfp] phase 1: scanning all team rosters (sp 2..{latest})...", flush=True)
    t0       = time.time()
    all_data = phase1_scan_all_teams(latest, two_way_names)

    print("[cumfp] phase 2: kona fallback for low-coverage players...", flush=True)
    extra    = phase2_kona_fallback(current_roster, all_data, latest)
    all_data.update(extra)

    elapsed = time.time() - t0
    n = write_csv(all_data, current_names, out_csv)
    write_meta(current_roster, meta_csv)
    print(f"[cumfp] wrote {n} rows + meta in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
