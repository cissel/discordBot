#!/usr/bin/env python3
"""
nflDraftWatch.py

Phase 4, step 8: LIVE draft-day CLI watcher. Polls a real Sleeper draft
(works for mocks AND real league drafts - same API, and draft_slot handling
already fixed to be unambiguous for both, see nflMockDraftLog.py) and prints
your optimal-pick recommendations after every new pick, ranked by MARGINAL
starting-lineup VORP gain (not raw VORP - a 3rd/4th roster slot at a
position you've already filled contributes ~0, same logic as
buildNflDraftStrategySim.py / buildNflStrategyGuide.py).

No UI, no Discord dependency - just run it in a terminal next to your
browser during the draft and re-read the printout each time it's near your
turn. Ctrl+C to stop.

USAGE:
  venv/bin/python3 python/nflDraftWatch.py <draft_id> <my_slot> [--poll 8] [--top 10] [--lookahead 1]

  draft_id    Sleeper draft ID (bare numeric ID, or paste the full draft URL -
              query strings/fragments are stripped automatically).
  my_slot     Your draft-order position for round 1 (e.g. "I have pick 10" -> 10).
  --poll      Seconds between polls (default 8). Sleeper's API is public/read-only;
              8-10s is plenty responsive without hammering it.
  --top       How many recommendations to print each cycle (default 10).
  --lookahead How many picks away your turn can be and still trigger the FULL
              recommendation list (default 2 = shows full list on your turn and
              the 2 picks right before it). Further out than that, prints a quiet
              1-line summary instead, so a multi-hour draft doesn't scroll a
              full ranked list every poll cycle for turns that are far away.

WHAT IT SHOWS EACH CYCLE:
  - Any new picks since last poll (round, pick #, who/what position, by which slot).
  - "YOUR TURN NOW" banner if the next pick belongs to your slot.
  - Your current roster + which starter slots are still unfilled.
  - Top-N available players ranked by marginal starting-lineup VORP gain
    (i.e. "if you took this player right now, how much does your OPTIMAL
    starting lineup improve" - correctly deprioritizes e.g. a 2nd elite QB
    once your 1 QB slot is filled, same as the strategy sim).

LIMITATIONS:
  - Marginal-value ranking evaluates each candidate in isolation against
    YOUR roster only - it does not model what will still be available by
    your NEXT pick (no opponent-behavior lookahead). Good for "what should
    I do right now", not "what will still be there in 2 rounds."
  - Same rookie-projection / injury-conflation caveats as the rest of the
    VORP board apply (see draft_board_2026.csv footnotes).

CONTEXT COLUMNS (added 2026-08, all optional/graceful - missing files just
mean that column comes back blank rather than crashing the tool):
  - adp_spread: |Sleeper ADP - ESPN ADP| for this player today (see
    buildAdpDispersion.py). CAVEAT: as of 2026-08, Sleeper's adp_dd_ppr is
    still an integer-quantized preseason RANK, not a true statistical
    average (confirmed via GraphQL schema introspection - no true
    continuous-ADP field currently exists in Sleeper's public API this
    early in the season) - read this as "Sleeper rank vs ESPN live ADP",
    not an apples-to-apples ADP-vs-ADP comparison yet. Requires
    espn_adp_2026.csv + adp_cross_platform_spread.csv (fetchEspnAdp.py,
    buildAdpDispersion.py).
  - adp_rank_stdev: day-over-day stdev of this player's Sleeper rank across
    accumulated adp_history.csv snapshots (needs 3+ days banked - blank
    until then). Same rank-not-true-ADP caveat as above.
  - pts_mean_2025 / pts_sd_2025 / pts_sharpe_2025: mean/stdev/Sharpe
    (mean/sd) of this player's ROOM 40-SCORED weekly fantasy points across
    the 2025 regular season (see buildNflWeeklyVolatility.R). Single-season
    only - small-sample stdev, same caveat as any 17-week-max estimate.
    Sharpe here means "weekly scoring consistency", NOT "good" - a
    low-volume role player can out-Sharpe a high-ceiling boom/bust starter,
    always read alongside pts_mean_2025, never instead of it. Blank for
    rookies (no 2025 NFL snaps to compute from).
"""
import argparse
import os
import re
import sys
import time
import unicodedata
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nflMockDraftLog import (
    FLEX_ELIGIBLE, STARTERS, MockDraftError, fetch_sleeper_draft, load_id_bridge,
    load_vorp_board, match_player, score_starting_lineup,
)
import requests

SLEEPER_PICKS_URL = "https://api.sleeper.app/v1/draft/{draft_id}/picks"
SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    """Same normalization convention used throughout the repo (buildNfl2026
    Projections.py, buildNflDraftBoard.py) - strip accents/suffixes so
    nflverse-sourced weekly volatility (different naming convention) can be
    joined onto Sleeper-sourced VORP rows."""
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_draft_context(vorp_df: pd.DataFrame) -> pd.DataFrame:
    """Merges in the optional context columns documented in the module
    docstring. Each source is independently optional - missing files print
    a one-line note and leave that column NaN rather than crashing the
    live tool mid-draft-prep."""
    out_dir = os.path.dirname(load_vorp_board.__globals__["VORP_PATH"])
    vorp_df = vorp_df.copy()

    spread_path = os.path.join(out_dir, "adp_cross_platform_spread.csv")
    if os.path.exists(spread_path):
        spread = pd.read_csv(spread_path)[["full_name", "position", "spread"]]
        spread = spread.drop_duplicates(subset=["full_name", "position"])
        spread = spread.rename(columns={"spread": "adp_spread"})
        vorp_df = vorp_df.merge(spread, on=["full_name", "position"], how="left")
        print(f"Loaded cross-platform ADP spread: {spread['adp_spread'].notna().sum()} matched "
              f"(Sleeper=rank-based preseason artifact, see docstring caveat)")
    else:
        vorp_df["adp_spread"] = pd.NA
        print("NOTE: adp_cross_platform_spread.csv not found - run fetchEspnAdp.py + "
              "buildAdpDispersion.py for cross-platform spread. Skipping that column.")

    vol_path = os.path.join(out_dir, "adp_volatility.csv")
    if os.path.exists(vol_path):
        adp_vol = pd.read_csv(vol_path)[["full_name", "position", "adp_rank_stdev"]]
        adp_vol = adp_vol.drop_duplicates(subset=["full_name", "position"])
        vorp_df = vorp_df.merge(adp_vol, on=["full_name", "position"], how="left")
        print(f"Loaded ADP rank volatility: {adp_vol['adp_rank_stdev'].notna().sum()} players "
              f"with enough history")
    else:
        vorp_df["adp_rank_stdev"] = pd.NA
        print("NOTE: adp_volatility.csv not found yet (needs 3+ days of adp_history.csv) - "
              "skipping that column for now.")

    weekly_path = os.path.join(out_dir, "weekly_volatility_2025.csv")
    if os.path.exists(weekly_path):
        weekly = pd.read_csv(weekly_path)
        weekly["norm_name"] = weekly["player_display_name"].map(norm_name)
        weekly = weekly.drop_duplicates(subset=["norm_name", "position"])
        weekly = weekly.rename(columns={
            "games_played": "games_2025", "mean_pts": "pts_mean_2025",
            "sd_pts": "pts_sd_2025", "sharpe_pts": "pts_sharpe_2025",
        })[["norm_name", "position", "games_2025", "pts_mean_2025", "pts_sd_2025", "pts_sharpe_2025"]]
        vorp_df["norm_name"] = vorp_df["full_name"].map(norm_name)
        vorp_df = vorp_df.merge(weekly, on=["norm_name", "position"], how="left")
        vorp_df = vorp_df.drop(columns=["norm_name"])
        print(f"Loaded 2025 weekly scoring volatility: {vorp_df['pts_mean_2025'].notna().sum()} "
              f"matched (blank for rookies - no 2025 NFL snaps)")
    else:
        vorp_df["pts_mean_2025"] = pd.NA
        vorp_df["pts_sd_2025"] = pd.NA
        vorp_df["pts_sharpe_2025"] = pd.NA
        vorp_df["games_2025"] = pd.NA
        print("NOTE: weekly_volatility_2025.csv not found - run r/buildNflWeeklyVolatility.R "
              "for 2025 weekly mean/sd/Sharpe. Skipping those columns.")

    return vorp_df


def fetch_picks_tolerant(draft_id: str) -> list:
    """Like nflMockDraftLog.fetch_sleeper_picks, but treats an empty picks
    list as a normal WAITING state (draft not started / no picks yet),
    not a fatal error - a live watcher polls before the draft starts and
    between individual picks, so an empty list is expected and routine,
    unlike the grading tool where it means something is actually wrong."""
    r = requests.get(SLEEPER_PICKS_URL.format(draft_id=draft_id), timeout=20)
    r.raise_for_status()
    picks = r.json()
    if not isinstance(picks, list):
        raise MockDraftError(
            f"Sleeper returned an unexpected response shape for draft '{draft_id}' "
            "picks (expected a list) - the draft ID is likely malformed."
        )
    return picks  # may be empty - caller handles that as "waiting", not an error

ADP_CAP = 250  # a bit looser than the pre-draft board - late-round dart
               # throws (handcuffs, streamers) are relevant live, unlike prep


def _extract_draft_id(raw: str) -> str:
    raw = raw.strip().split("?")[0].split("#")[0].rstrip("/")
    return raw.split("/")[-1]


def predict_next_slot(pick_no: int, n_teams: int) -> int:
    """Standard snake-draft slot prediction for the given upcoming pick
    number (1-indexed). Matches Sleeper's draft_slot convention (confirmed
    against real Room 40 draft data during the mockdraft-grade bug fix)."""
    round_num = (pick_no - 1) // n_teams + 1
    pos_in_round = (pick_no - 1) % n_teams + 1
    if round_num % 2 == 1:
        return pos_in_round
    return n_teams - pos_in_round + 1


def picks_until_my_turn(next_pick_no: int, my_slot: int, n_teams: int, max_lookahead: int = 30) -> int:
    """0 = next_pick_no IS my turn. 1 = one pick away. etc. Scans forward
    (bounded) rather than doing snake-order algebra directly, since the
    snake direction flips each round and it's easy to get the off-by-one
    wrong doing it analytically - scanning is slower but unambiguous."""
    for offset in range(max_lookahead):
        if predict_next_slot(next_pick_no + offset, n_teams) == my_slot:
            return offset
    return max_lookahead  # shouldn't happen in a normal 12-team draft


def roster_needs_str(roster_counts: dict) -> str:
    parts = []
    for pos, n in STARTERS.items():
        have = roster_counts.get(pos, 0)
        if have < n:
            parts.append(f"{pos} ({have}/{n})")
    flex_have = sum(roster_counts.get(p, 0) for p in FLEX_ELIGIBLE) - sum(
        min(roster_counts.get(p, 0), n) for p, n in STARTERS.items() if p in FLEX_ELIGIBLE
    )
    if not parts:
        parts.append(f"FLEX ({'filled' if flex_have >= 1 else 'open'})")
    return ", ".join(parts) if parts else "all starter slots filled"


def recommend(my_roster: list, available_df: pd.DataFrame, top_n: int,
               roster_counts: dict = None) -> pd.DataFrame:
    """Ranks available players by marginal starting-lineup VORP gain.

    HARD EXCLUDE for positions that are (a) already at their starter cap and
    (b) not FLEX-eligible - i.e. QB in this league. Unlike RB/WR/TE, which
    can still start via FLEX even after their dedicated slots are full (so
    a 3rd RB has real, if smaller, value as bye-week/injury bench depth), a
    2nd QB has ZERO possible use once your 1 QB slot is filled - there's no
    flex spot for it and no realistic scenario where you'd start it. James
    confirmed he only ever drafts one QB. Before this fix, once-gain-is-zero
    ties fell back to raw VORP, and backup QBs have inflated VORP purely
    because the QB replacement baseline is shallow (same root cause as the
    buildNflDraftStrategySim.py QB-hoarding bug already fixed there) - so
    they kept reappearing in the recommendation list even with 0 real value.
    """
    roster_counts = roster_counts or {}
    candidates_df = available_df
    if roster_counts:
        capped_non_flex = {
            pos for pos, need in STARTERS.items()
            if pos not in FLEX_ELIGIBLE and roster_counts.get(pos, 0) >= need
        }
        if capped_non_flex:
            candidates_df = candidates_df[~candidates_df["position"].isin(capped_non_flex)]

    baseline = score_starting_lineup(my_roster)
    shortlist = (candidates_df.sort_values("vorp", ascending=False)
                 .groupby("position", group_keys=False).head(top_n + 3))
    recs = []
    for _, cand in shortlist.iterrows():
        trial = my_roster + [cand.to_dict()]
        gain = score_starting_lineup(trial) - baseline
        recs.append({
            "full_name": cand["full_name"], "position": cand["position"],
            "team": cand.get("team", ""), "adp_overall": cand["adp_overall"],
            "vorp": cand["vorp"], "value_gap": cand["value_gap"],
            "marginal_gain": round(gain, 1),
            # elite_mover_2026: changed teams for 2026 carrying a big (25%+)
            # target share - see buildNflVorp.py for full rationale. The
            # model only applies a modest discount here (tested a sharper
            # "destination target competition" feature and found it wasn't
            # statistically significant) - flag for manual double-check
            # rather than trust the number blindly, same as injury flag.
            "mover": bool(cand.get("elite_mover_2026", False)),
            # Context columns added 2026-08 (see module docstring for full
            # caveats) - all optional, NaN if the source file wasn't built.
            "adp_spread": cand.get("adp_spread", pd.NA),
            "adp_rank_stdev": cand.get("adp_rank_stdev", pd.NA),
            "pts_mean_2025": cand.get("pts_mean_2025", pd.NA),
            "pts_sd_2025": cand.get("pts_sd_2025", pd.NA),
            "pts_sharpe_2025": cand.get("pts_sharpe_2025", pd.NA),
        })
    out = pd.DataFrame(recs).sort_values(
        ["marginal_gain", "vorp"], ascending=[False, False]
    ).head(top_n)
    for col in ("adp_spread", "adp_rank_stdev", "pts_mean_2025", "pts_sd_2025", "pts_sharpe_2025"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(1)
    return out


def main():
    ap = argparse.ArgumentParser(description="Live Sleeper draft watcher - CLI recommendations.")
    ap.add_argument("draft_id")
    ap.add_argument("my_slot", type=int)
    ap.add_argument("--poll", type=int, default=8)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--lookahead", type=int, default=2,
                     help="Show the FULL recommendation list when your turn is this many "
                          "picks away or closer (default 2 = your turn plus the 2 picks "
                          "right before it). Further out, print a quiet 1-line summary "
                          "instead so a multi-hour draft doesn't scroll a full ranked "
                          "list every poll cycle for turns that are far away.")
    args = ap.parse_args()

    draft_id = _extract_draft_id(args.draft_id)
    vorp_df = load_vorp_board()
    vorp_df = vorp_df[vorp_df["adp_overall"] <= ADP_CAP].copy()
    vorp_df = load_draft_context(vorp_df)
    match_player._id_bridge = load_id_bridge()

    meta = fetch_sleeper_draft(draft_id)
    settings = meta.get("settings", {})
    n_teams = settings.get("teams", 12)
    n_rounds = settings.get("rounds", 15)
    total_picks_expected = n_teams * n_rounds

    print(f"Watching draft {draft_id} — {n_teams} teams, {n_rounds} rounds, you are slot {args.my_slot}.")
    print(f"Polling every {args.poll}s. Ctrl+C to stop.\n")

    seen_pick_nos = set()

    try:
        while True:
            try:
                picks = fetch_picks_tolerant(draft_id)
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] poll failed: {e} — retrying next cycle.")
                time.sleep(args.poll)
                continue

            if not picks:
                print(f"[{datetime.now():%H:%M:%S}] Draft not started yet (0 picks so far) — waiting...")
                time.sleep(args.poll)
                continue

            new_picks = [p for p in picks if p.get("pick_no") not in seen_pick_nos]
            if new_picks:
                print(f"--- New picks ({datetime.now():%H:%M:%S}) ---")
                for p in sorted(new_picks, key=lambda x: x.get("pick_no", 0)):
                    meta_p = p.get("metadata", {}) or {}
                    first, last = meta_p.get("first_name", ""), meta_p.get("last_name", "")
                    pos = meta_p.get("position", p.get("position", "?"))
                    mine = " <-- YOU" if p.get("draft_slot") == args.my_slot else ""
                    print(f"  Rd{p.get('round')} Pick#{p.get('pick_no')} "
                          f"(slot {p.get('draft_slot')}): {first} {last} ({pos}){mine}")
                seen_pick_nos.update(p.get("pick_no") for p in new_picks)

            # build my roster + drafted set
            drafted_names = set()
            my_rows = []
            for p in picks:
                meta_p = p.get("metadata", {}) or {}
                first, last = meta_p.get("first_name", ""), meta_p.get("last_name", "")
                position = meta_p.get("position", p.get("position", ""))
                row = match_player(vorp_df, p.get("player_id"), first, last, position)
                if row is None:
                    continue
                drafted_names.add(row["full_name"])
                if p.get("draft_slot") == args.my_slot:
                    my_rows.append(row.to_dict())

            roster_counts = {}
            for r in my_rows:
                roster_counts[r["position"]] = roster_counts.get(r["position"], 0) + 1

            available = vorp_df[~vorp_df["full_name"].isin(drafted_names)].copy()

            n_picks_made = len(picks)
            if n_picks_made >= total_picks_expected:
                print("\nDraft complete — all picks made. Exiting.")
                break

            next_pick_no = n_picks_made + 1
            next_slot = predict_next_slot(next_pick_no, n_teams)
            distance = picks_until_my_turn(next_pick_no, args.my_slot, n_teams)

            if distance > args.lookahead:
                # Far from my turn - quiet 1-line summary so a multi-hour
                # draft doesn't scroll a full rec list every 8s for no reason.
                top1 = recommend(my_rows, available, 1, roster_counts)
                best_name = top1.iloc[0]["full_name"] if not top1.empty else "n/a"
                print(f"[{datetime.now():%H:%M:%S}] Pick #{next_pick_no} up (slot {next_slot}) — "
                      f"{distance} picks until your turn. Current top pick if drafting now: {best_name}")
                time.sleep(args.poll)
                continue

            print(f"\n[{datetime.now():%H:%M:%S}] Pick #{next_pick_no} is up next (slot {next_slot}).")
            if next_slot == args.my_slot:
                print(">>> YOUR TURN NOW <<<")
            else:
                print(f">>> Your turn in {distance} pick(s) - get ready <<<")
            print(f"Your roster so far ({len(my_rows)} picks): "
                  + (", ".join(f"{r['full_name']} ({r['position']})" for r in my_rows) or "none yet"))
            print(f"Still need: {roster_needs_str(roster_counts)}")

            recs = recommend(my_rows, available, args.top, roster_counts)
            print(f"\nTop {len(recs)} recommendations right now (by marginal starting-lineup VORP gain):")
            print(recs.to_string(index=False))
            if recs["mover"].any():
                print("(mover=True: changed teams carrying a big target share - model applies only "
                      "a modest discount here, research manually before trusting the number)")
            if recs["adp_spread"].notna().any() or recs["adp_rank_stdev"].notna().any():
                print("(adp_spread/adp_rank_stdev: Sleeper's ADP is still an integer-quantized "
                      "preseason rank, not a true statistical average - read as directional market "
                      "signal, not precise ADP-vs-ADP math, until it gains real decimal precision "
                      "later in August)")
            if recs["pts_sharpe_2025"].notna().any():
                print("(pts_sharpe_2025: weekly SCORING CONSISTENCY, not \"good\" - a low-volume role "
                      "player can out-Sharpe a boom/bust starter. Always read next to pts_mean_2025, "
                      "single 2025 season only)")
            print("=" * 100)

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
