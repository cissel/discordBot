#!/usr/bin/env python3
"""
worldSilliesGMScore.py
Grades each manager in the World Sillies ESPN baseball league on their
decisions across the full season (all completed matchup periods).

Components (equal-weight z-score composite):
  1. Draft VOE      — sum(player_season_pts - round_median) across all picks
  2. Lineup Eff     — avg(actual_starter_pts / optimal_pts) per scoring period
  3. Waiver/Trade   — net pts from recent transactions (kona_league_communication)
  4. Record Score   — win% + PF z-score blend

Writes:
  ~/discordBot/outputs/sports/mlb/fantasy/gm_scores.json   — embed data
  ~/discordBot/outputs/sports/mlb/fantasy/gm_scores.csv    — for R plot
"""

import os, sys, json, datetime, requests, unicodedata, time
from pathlib import Path
from collections import defaultdict

BASE        = Path(os.path.expanduser("~/discordBot"))
FANTASY_DIR = BASE / "outputs/sports/mlb/fantasy"
OUT_JSON    = FANTASY_DIR / "gm_scores.json"
OUT_CSV     = FANTASY_DIR / "gm_scores.csv"
EFF_CACHE   = FANTASY_DIR / "gm_eff_cache.json"
FANTASY_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_YEAR = datetime.date.today().year
MLB_API      = "https://statsapi.mlb.com/api/v1"
ESPN_BASE    = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/1858112591"
HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)"}
LEAGUE_ID    = 1858112591

# Lineup slot IDs
BENCH_SLOT  = 16
IL_SLOT     = 17
BENCH_SLOTS = {BENCH_SLOT, IL_SLOT}


def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("ascii").strip().lower()


def espn_get(params=None, extend=""):
    url = ESPN_BASE + extend
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def grade_letter(score: float) -> str:
    if score >= 1.5:  return "A+"
    if score >= 1.0:  return "A"
    if score >= 0.5:  return "A-"
    if score >= 0.2:  return "B+"
    if score >= -0.2: return "B"
    if score >= -0.5: return "B-"
    if score >= -0.8: return "C+"
    if score >= -1.2: return "C"
    if score >= -1.5: return "C-"
    if score >= -2.0: return "D"
    return "F"


def z_score(values: list[float]) -> list[float]:
    """Return z-scores. Returns zeros if no variance."""
    import statistics
    if len(values) < 2:
        return [0.0] * len(values)
    mu = statistics.mean(values)
    sd = statistics.stdev(values)
    if sd == 0:
        return [0.0] * len(values)
    return [(v - mu) / sd for v in values]


def main():
    from espn_api.baseball import League

    league   = League(league_id=LEAGUE_ID, year=CURRENT_YEAR)
    req      = league.espn_request
    team_map = {t.team_id: t.team_name for t in league.teams}

    # ── 1. Schedule: build MP->SPs map and team records ───────────────────────
    print("[gmscore] fetching schedule...", flush=True)
    sched_data = req.league_get(params={"view": ["mMatchup", "mMatchupScore"]})
    schedule   = sched_data.get("schedule", [])

    mp_to_sps    = defaultdict(set)   # mp -> {scoring_period_ids}
    team_records = defaultdict(lambda: {"wins": 0, "losses": 0, "pts": 0.0, "opp_pts": 0.0})
    completed_mps = set()

    for e in schedule:
        mp     = e.get("matchupPeriodId")
        winner = e.get("winner", "UNDECIDED")
        home   = e.get("home", {})
        away   = e.get("away", {})
        h_id   = home.get("teamId")
        a_id   = away.get("teamId")
        h_pts  = float(home.get("totalPoints", 0) or 0)
        a_pts  = float(away.get("totalPoints", 0) or 0)

        for side in ("home", "away"):
            psp = e.get(side, {}).get("pointsByScoringPeriod", {})
            mp_to_sps[mp].update(int(k) for k in psp.keys())

        if winner in ("HOME", "AWAY") and h_id and a_id:
            completed_mps.add(mp)
            team_records[h_id]["pts"]     += h_pts
            team_records[h_id]["opp_pts"] += a_pts
            team_records[a_id]["pts"]     += a_pts
            team_records[a_id]["opp_pts"] += h_pts
            if winner == "HOME":
                team_records[h_id]["wins"]   += 1
                team_records[a_id]["losses"] += 1
            else:
                team_records[a_id]["wins"]   += 1
                team_records[h_id]["losses"] += 1

    n_weeks = len(completed_mps)
    print(f"[gmscore] {n_weeks} completed matchup periods", flush=True)
    if n_weeks == 0:
        print("[gmscore] season hasn't started yet", file=sys.stderr)
        sys.exit(1)

    all_sps = sorted(sp for mp in completed_mps for sp in mp_to_sps[mp])
    print(f"[gmscore] scoring periods to process: {min(all_sps)}-{max(all_sps)} ({len(all_sps)} days)", flush=True)

    # ── 2. Draft VOE ──────────────────────────────────────────────────────────
    print("[gmscore] fetching draft picks...", flush=True)
    draft_data = req.league_get(params={"view": ["mDraftDetail"]})
    picks      = draft_data["draftDetail"]["picks"]

    # Resolve player names and season fantasy pts from existing playerData CSVs
    import csv as _csv
    pitcher_pts = {}
    batter_pts  = {}
    for fname, store in [("pitcher_season_summary.csv", pitcher_pts),
                         ("batter_season_summary.csv",  batter_pts)]:
        path = FANTASY_DIR / "playerData" / fname
        if path.exists():
            with open(path, newline="") as f:
                for row in _csv.DictReader(f):
                    store[norm(row["player_name"])] = float(row.get("fantasy_pts", 0) or 0)

    # Resolve espn player IDs -> names using the league's player_map
    player_id_to_name = {}
    if hasattr(league, 'player_map'):
        for pid, player in league.player_map.items():
            player_id_to_name[pid] = getattr(player, 'name', str(pid))
    # Fallback: fetch names from the roster entries we'll pull anyway
    for team in league.teams:
        for p in team.roster:
            player_id_to_name[p.playerId] = p.name

    # Also bulk-fetch via ESPN player endpoint for drafted players not on rosters
    draft_player_ids = set(p["playerId"] for p in picks)
    missing_ids = draft_player_ids - set(player_id_to_name.keys())
    if missing_ids:
        try:
            chunk = list(missing_ids)[:50]
            ids_str = ",".join(str(i) for i in chunk)
            r = requests.get(f"{MLB_API}/people", params={"personIds": ids_str}, headers=HEADERS, timeout=8)
            for person in r.json().get("people", []):
                # This is MLB API, won't have ESPN IDs - skip
                pass
        except Exception:
            pass

    # Build round medians for VOE
    round_pts = defaultdict(list)
    for pick in picks:
        pid  = pick["playerId"]
        rnd  = pick["roundId"]
        name = player_id_to_name.get(pid, "")
        nkey = norm(name)
        pts  = pitcher_pts.get(nkey) or batter_pts.get(nkey) or 0.0
        round_pts[rnd].append(pts)

    import statistics
    round_median = {rnd: statistics.median(pts) for rnd, pts in round_pts.items() if pts}

    # Compute per-team draft VOE
    draft_voe = defaultdict(float)
    draft_details = defaultdict(list)  # team_id -> [(name, rnd, pts, voe)]
    for pick in picks:
        pid    = pick["playerId"]
        rnd    = pick["roundId"]
        tid    = pick["teamId"]
        name   = player_id_to_name.get(pid, f"ID#{pid}")
        nkey   = norm(name)
        pts    = pitcher_pts.get(nkey) or batter_pts.get(nkey) or 0.0
        med    = round_median.get(rnd, 0.0)
        voe    = pts - med
        draft_voe[tid]    += voe
        draft_details[tid].append({"name": name, "round": rnd, "pts": round(pts, 1), "voe": round(voe, 1)})

    print(f"[gmscore] draft VOE computed for {len(draft_voe)} teams", flush=True)

    # ── 3. Lineup Efficiency (cached per scoring period) ─────────────────────
    print("[gmscore] computing lineup efficiency...", flush=True)

    # Load cache
    eff_cache = {}  # "teamId_sp" -> {"starter_pts": float, "optimal_pts": float}
    if EFF_CACHE.exists():
        try:
            with open(EFF_CACHE) as f:
                eff_cache = json.load(f)
        except Exception:
            eff_cache = {}

    # Fetch missing SPs — all teams in ONE request per SP (8x faster than per-team)
    missing_sps = sorted({sp for tid in team_map for sp in all_sps if f"{tid}_{sp}" not in eff_cache})
    n_missing = len(missing_sps) * len(team_map)
    print(f"[gmscore] fetching {len(missing_sps)} missing scoring periods ({n_missing} team-days)...", flush=True)

    for sp in missing_sps:
        try:
            params  = {"view": ["mTeam", "mRoster"], "scoringPeriodId": sp}
            sp_data = req.league_get(params=params)
            for team_entry in sp_data.get("teams", []):
                tid     = team_entry.get("id")
                if tid not in team_map:
                    continue
                entries  = team_entry.get("roster", {}).get("entries", [])
                starters = [e for e in entries if e["lineupSlotId"] not in BENCH_SLOTS]
                bench    = [e for e in entries if e["lineupSlotId"] == BENCH_SLOT]

                def pts_of(e):
                    return float(e["playerPoolEntry"].get("appliedStatTotal", 0) or 0)

                starter_pts = sum(pts_of(e) for e in starters)
                all_avail   = sorted([pts_of(e) for e in starters + bench], reverse=True)
                optimal_pts = sum(all_avail[:len(starters)])

                eff_cache[f"{tid}_{sp}"] = {
                    "starter_pts": round(starter_pts, 2),
                    "optimal_pts": round(optimal_pts, 2),
                }
        except Exception as ex:
            print(f"  [warn] SP {sp}: {ex}", file=sys.stderr)
        time.sleep(0.05)

    # Save updated cache
    with open(EFF_CACHE, "w") as f:
        json.dump(eff_cache, f)

    # Aggregate efficiency per team
    team_eff = {}
    for tid in team_map:
        days_data = [eff_cache[f"{tid}_{sp}"] for sp in all_sps if f"{tid}_{sp}" in eff_cache]
        valid     = [d for d in days_data if d["optimal_pts"] > 0]
        if valid:
            avg_eff = sum(d["starter_pts"] / d["optimal_pts"] for d in valid) / len(valid)
            avg_pts_left = sum(d["optimal_pts"] - d["starter_pts"] for d in valid) / len(valid)
        else:
            avg_eff = 1.0
            avg_pts_left = 0.0
        team_eff[tid] = {"avg_eff": round(avg_eff, 4), "avg_pts_left": round(avg_pts_left, 2), "n_days": len(valid)}

    print(f"[gmscore] lineup efficiency computed", flush=True)

    # ── 4. Waiver / Trade net pts (recent activity via kona_league_communication) ──
    print("[gmscore] fetching recent transactions...", flush=True)

    # Get player IDs -> season pts lookup
    all_player_pts = {**pitcher_pts, **batter_pts}

    txn_net    = defaultdict(float)  # team_id -> net pts from adds/trades
    txn_count  = defaultdict(int)    # team_id -> number of add transactions
    trade_net  = defaultdict(float)  # team_id -> net pts from trades specifically

    try:
        r = requests.get(ESPN_BASE, headers=HEADERS,
                         params={"view": "kona_league_communication"}, timeout=15)
        if r.status_code == 200:
            topics = [t for t in r.json().get("communication", {}).get("topics", [])
                      if t.get("type") == "ACTIVITY_TRANSACTIONS"]

            # Build a reverse player ID -> season pts lookup using draft data
            pid_to_pts = {}
            for pid, name in player_id_to_name.items():
                nkey = norm(name)
                pts  = pitcher_pts.get(nkey) or batter_pts.get(nkey) or 0.0
                pid_to_pts[pid] = pts

            for topic in topics:
                msgs     = topic.get("messages", [])
                msg_type_ids = set(m.get("messageTypeId") for m in msgs)

                if 188 in msg_type_ids:
                    # TRADE - compute net for each team
                    by_from_to = defaultdict(list)
                    for m in msgs:
                        if m.get("messageTypeId") == 188:
                            pair = (m.get("from"), m.get("to"))
                            by_from_to[pair].append(m.get("targetId", 0))
                    # pairs: (giver_team, receiver_team) -> [player_ids_given]
                    # Net for receiver = pts_received - pts_given
                    team_received = defaultdict(float)
                    team_gave     = defaultdict(float)
                    for (from_t, to_t), pids in by_from_to.items():
                        for pid in pids:
                            pts = pid_to_pts.get(pid, 0.0)
                            team_gave[from_t]     += pts
                            team_received[to_t]   += pts
                    for tid in set(list(team_gave.keys()) + list(team_received.keys())):
                        if tid and tid > 0:
                            trade_net[tid] += team_received.get(tid, 0) - team_gave.get(tid, 0)
                            txn_net[tid]   += team_received.get(tid, 0) - team_gave.get(tid, 0)

                elif 178 in msg_type_ids or 239 in msg_type_ids:
                    # WAIVER/FA add
                    for m in msgs:
                        mid = m.get("messageTypeId")
                        if mid in (178, 239):
                            tid = m.get("to")
                            pid = m.get("targetId", 0)
                            if tid and tid > 0:
                                pts = pid_to_pts.get(pid, 0.0)
                                txn_net[tid]   += pts
                                txn_count[tid] += 1
                        elif mid == 179:  # drop
                            tid = m.get("from")
                            pid = m.get("targetId", 0)
                            if tid and tid > 0:
                                pts = pid_to_pts.get(pid, 0.0)
                                txn_net[tid] -= pts

            print(f"[gmscore] parsed {len(topics)} transaction topics", flush=True)
        else:
            print(f"[gmscore] kona_league_communication status={r.status_code}", file=sys.stderr)
    except Exception as ex:
        print(f"[gmscore] transaction fetch failed: {ex}", file=sys.stderr)

    # ── 5. Assemble and z-score ───────────────────────────────────────────────
    team_ids = [t.team_id for t in league.teams]

    raw_draft   = [draft_voe.get(tid, 0.0)           for tid in team_ids]
    raw_eff     = [team_eff.get(tid, {}).get("avg_eff", 0.9) for tid in team_ids]
    raw_txn     = [txn_net.get(tid, 0.0)             for tid in team_ids]
    raw_trade   = [trade_net.get(tid, 0.0)           for tid in team_ids]
    raw_wins    = [team_records[tid]["wins"]          for tid in team_ids]
    raw_pf      = [team_records[tid]["pts"]           for tid in team_ids]

    # Record score = 0.7*win_pct + 0.3*pf_pct
    total_games = max(max(r["wins"] + r["losses"] for r in team_records.values()), 1)
    raw_record  = [
        0.7 * (team_records[tid]["wins"] / max(team_records[tid]["wins"] + team_records[tid]["losses"], 1))
        + 0.3 * (team_records[tid]["pts"] / max(raw_pf))
        for tid in team_ids
    ]

    z_draft  = z_score(raw_draft)
    z_eff    = z_score(raw_eff)
    z_txn    = z_score(raw_txn)
    z_record = z_score(raw_record)

    results = []
    for i, tid in enumerate(team_ids):
        rec  = team_records[tid]
        eff  = team_eff.get(tid, {})
        gm   = z_draft[i] + z_eff[i] + z_txn[i] + z_record[i]

        # Top and worst VOE picks
        picks_sorted = sorted(draft_details[tid], key=lambda p: -p["voe"])
        top_pick  = picks_sorted[0]  if picks_sorted else None
        worst_pick = picks_sorted[-1] if picks_sorted else None

        results.append({
            "team_name":   team_map[tid],
            "team_id":     tid,
            "gm_score":    round(gm, 3),
            "grade":       grade_letter(gm),
            "wins":        rec["wins"],
            "losses":      rec["losses"],
            "pts":         round(rec["pts"], 1),
            "opp_pts":     round(rec["opp_pts"], 1),
            "draft_voe":   round(raw_draft[i], 1),
            "avg_eff":     round(raw_eff[i], 4),
            "avg_pts_left": round(eff.get("avg_pts_left", 0.0), 2),
            "txn_net":     round(raw_txn[i], 1),
            "trade_net":   round(raw_trade[i], 1),
            "record_score": round(raw_record[i], 4),
            "z_draft":     round(z_draft[i], 3),
            "z_eff":       round(z_eff[i], 3),
            "z_txn":       round(z_txn[i], 3),
            "z_record":    round(z_record[i], 3),
            "n_days":      eff.get("n_days", 0),
            "txn_count":   txn_count.get(tid, 0),
            "top_pick":    top_pick,
            "worst_pick":  worst_pick,
        })

    results.sort(key=lambda x: -x["gm_score"])

    # ── 6. Write JSON (for embed) ─────────────────────────────────────────────
    out = {
        "generated":       datetime.datetime.now().isoformat(),
        "n_weeks":         n_weeks,
        "txn_note":        "recent transactions only (no ESPN auth)",
        "teams":           results,
    }
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)

    # ── 7. Write CSV (for R plot) ─────────────────────────────────────────────
    import csv
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "team_name", "gm_score", "grade",
            "z_draft", "z_eff", "z_txn", "z_record",
            "draft_voe", "avg_eff", "txn_net", "wins", "losses", "pts"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in writer.fieldnames})

    # ── 8. Print summary ──────────────────────────────────────────────────────
    print(f"\n[gmscore] GM Report - World Sillies {CURRENT_YEAR} ({n_weeks} weeks)")
    print(f"{'RANK':4} {'GRADE':5} {'GM':6}  {'TEAM':30} {'W-L':6} {'PTS':7}  {'VOE':7}  {'EFF%':6}  {'TXN':6}")
    print("-" * 90)
    for i, t in enumerate(results, 1):
        wl  = f"{t['wins']}-{t['losses']}"
        eff = f"{t['avg_eff']*100:.1f}%"
        print(f"{i:4} {t['grade']:5} {t['gm_score']:+6.2f}  {t['team_name']:30} {wl:6} {t['pts']:7.0f}  {t['draft_voe']:+7.1f}  {eff:6}  {t['txn_net']:+6.1f}")

    print(f"\nok - {OUT_JSON}")
    print(f"ok - {OUT_CSV}")


if __name__ == "__main__":
    main()
