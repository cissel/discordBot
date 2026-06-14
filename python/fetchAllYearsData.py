"""
fetchAllYearsData.py
Fetches Room 40 fantasy football data across all seasons:
  ESPN:   2022, 2023, 2024  (league 877643982)
  Sleeper: 2025             (league 1259616442014244864)
Outputs /tmp/room40_all_years.json
"""

import requests, json, time
from collections import defaultdict
from espn_api.football import League as EspnLeague

# ── Owner identity map ──────────────────────────────────────────────────────
# Key: canonical owner_id used throughout the output
# Values: all real names / ESPN owner strings / Sleeper display_names that
#         resolve to this person
OWNER_MAP = {
    "jhcv":         {"real": "James Cissel",    "aliases": ["james cissel", "jhcv"]},
    "zwilly12":     {"real": "Zach Williams",   "aliases": ["zach williams", "zwilly12"]},
    "TylerFello":   {"real": "Tyler Fello",     "aliases": ["tyler fello", "tylerfello"]},
    "tejordan71":   {"real": "Tyler Jordan",    "aliases": ["tyler jordan", "tejordan71"]},
    "MGivens":      {"real": "Marcus Givens",   "aliases": ["marcus givens", "mgivens"]},
    "EliStarfunk":  {"real": "Eli Danser",      "aliases": ["eli danser", "elistarfunk"]},
    "LeChuckski":   {"real": "Charlie King",    "aliases": ["charlie king", "lechuckski"]},
    "CountDoobie":  {"real": "Hunter Fishback", "aliases": ["hunter fishback", "countdoobie"]},
    "MullyMammoth": {"real": "Josh Mullis",     "aliases": ["joshua mullis", "josh mullis", "mullymammoth"]},
    "SquidlySounds":{"real": "Bryce White",     "aliases": ["bryce white", "squidlysounds"]},
    "comfyboxers":  {"real": "Chevy Oakes",     "aliases": ["chevy oakes", "charles oakes", "comfyboxers"]},
    "GrooveInn":    {"real": "Hunter Hileman",  "aliases": ["hunter hileman", "grooveinn"]},
    # ESPN-only members (no Sleeper equivalent)
    "MHimebauch":   {"real": "Michael Himebauch","aliases": ["michael himebauch"]},
    "NBeers":       {"real": "Nathan Beers",    "aliases": ["nathan beers"]},
    "BGleichenhaus":{"real": "Ben Gleichenhaus","aliases": ["ben gleichenhaus", "ben  gleichenhaus"]},
}

def resolve_owner(name_str):
    """Map a raw ESPN owner name string to canonical owner_id."""
    n = name_str.strip().lower()
    for oid, info in OWNER_MAP.items():
        if any(alias in n or n in alias for alias in info["aliases"]):
            return oid
    return name_str  # fallback: use raw string

# ── ESPN helpers ────────────────────────────────────────────────────────────
ESPN_LEAGUE_ID = 877643982
ESPN_YEARS     = [2022, 2023, 2024]

def espn_owner_str(team):
    if team.owners:
        return " ".join(f"{o['firstName']} {o['lastName']}" for o in team.owners)
    return team.team_name

def fetch_espn_year(year):
    print(f"  Fetching ESPN {year}...")
    league = EspnLeague(league_id=ESPN_LEAGUE_ID, year=year)

    teams_out = []
    for t in league.teams:
        raw_owner = espn_owner_str(t)
        owner_id  = resolve_owner(raw_owner)
        teams_out.append({
            "owner_id":   owner_id,
            "real_name":  OWNER_MAP.get(owner_id, {}).get("real", raw_owner),
            "team_name":  t.team_name,
            "wins":       t.wins,
            "losses":     t.losses,
            "points_for": round(t.points_for, 2),
            "points_against": round(t.points_against, 2),
            "final_standing": t.final_standing,
        })

    # Matchup results (regular season + playoffs)
    matchups_out = {}
    for wk in range(1, 19):
        try:
            box_scores = league.box_scores(wk)
            if not box_scores:
                break
            wk_list = []
            for bs in box_scores:
                if bs.home_team == 0 or bs.away_team == 0:
                    continue
                wk_list.append({
                    "home_owner": resolve_owner(espn_owner_str(bs.home_team)),
                    "home_team":  bs.home_team.team_name,
                    "home_score": round(bs.home_score, 2),
                    "away_owner": resolve_owner(espn_owner_str(bs.away_team)),
                    "away_team":  bs.away_team.team_name,
                    "away_score": round(bs.away_score, 2),
                    "is_playoff": wk > league.settings.reg_season_count,
                })
            if wk_list:
                matchups_out[str(wk)] = wk_list
        except Exception:
            break

    # Draft picks
    draft_out = []
    try:
        n_teams = len(league.teams)
        for pick in league.draft:
            raw_owner = espn_owner_str(pick.team)
            pick_no = (pick.round_num - 1) * n_teams + pick.round_pick
            draft_out.append({
                "pick_no":    pick_no,
                "round":      pick.round_num,
                "round_pick": pick.round_pick,
                "owner_id":   resolve_owner(raw_owner),
                "team_name":  pick.team.team_name,
                "player_name": pick.playerName,
                "position":   "",  # not available via espn_api BasePick
            })
    except Exception as e:
        print(f"    Draft fetch failed for {year}: {e}")

    return {
        "source":   "espn",
        "year":     year,
        "teams":    teams_out,
        "matchups": matchups_out,
        "draft":    draft_out,
        "reg_season_weeks": league.settings.reg_season_count,
        "playoff_teams":    league.settings.playoff_team_count,
    }

# ── Sleeper helpers ─────────────────────────────────────────────────────────
SLEEPER_LEAGUE_ID = "1259616442014244864"
BASE = "https://api.sleeper.app/v1"

def fetch_sleeper_year():
    print("  Fetching Sleeper 2025...")
    league_info = requests.get(f"{BASE}/league/{SLEEPER_LEAGUE_ID}", timeout=20).json()
    users       = requests.get(f"{BASE}/league/{SLEEPER_LEAGUE_ID}/users", timeout=20).json()
    rosters_raw = requests.get(f"{BASE}/league/{SLEEPER_LEAGUE_ID}/rosters", timeout=20).json()

    user_map = {u["user_id"]: u.get("display_name","") for u in users}

    roster_index = {}  # roster_id -> owner_id + team_name
    for r in rosters_raw:
        dn = user_map.get(r["owner_id"], r["owner_id"])
        roster_index[str(r["roster_id"])] = {
            "owner_id":   resolve_owner(dn),
            "display_name": dn,
            "team_name":  r.get("metadata", {}).get("team_name", "") or dn,
            "wins":       r["settings"].get("wins", 0),
            "losses":     r["settings"].get("losses", 0),
            "points_for": r["settings"].get("fpts", 0) + r["settings"].get("fpts_decimal", 0) / 100,
            "points_against": r["settings"].get("fpts_against", 0) + r["settings"].get("fpts_against_decimal", 0) / 100,
        }

    teams_out = list(roster_index.values())

    # Matchups
    matchups_out = {}
    reg_weeks = league_info.get("settings", {}).get("playoff_week_start", 15) - 1
    for wk in range(1, 18):
        wk_data = requests.get(f"{BASE}/league/{SLEEPER_LEAGUE_ID}/matchups/{wk}", timeout=20).json()
        if not wk_data:
            break
        by_mid = defaultdict(list)
        for m in wk_data:
            by_mid[m["matchup_id"]].append(m)
        wk_list = []
        for mid, pair in by_mid.items():
            if len(pair) < 2 or mid is None:
                continue
            a, b = pair[0], pair[1]
            ra = roster_index.get(str(a["roster_id"]), {})
            rb = roster_index.get(str(b["roster_id"]), {})
            wk_list.append({
                "home_owner": ra.get("owner_id", ""),
                "home_team":  ra.get("team_name", ""),
                "home_score": round(float(a.get("points", 0)), 2),
                "away_owner": rb.get("owner_id", ""),
                "away_team":  rb.get("team_name", ""),
                "away_score": round(float(b.get("points", 0)), 2),
                "is_playoff": wk > reg_weeks,
            })
        if wk_list:
            matchups_out[str(wk)] = wk_list
        time.sleep(0.05)

    # Draft
    draft_id   = league_info.get("draft_id")
    draft_raw  = requests.get(f"{BASE}/draft/{draft_id}/picks", timeout=30).json()
    draft_out  = []
    for p in draft_raw:
        meta = p.get("metadata", {})
        rid  = str(p.get("roster_id", ""))
        ri   = roster_index.get(rid, {})
        draft_out.append({
            "pick_no":    p["pick_no"],
            "round":      p["round"],
            "round_pick": p["draft_slot"],
            "owner_id":   ri.get("owner_id", rid),
            "team_name":  ri.get("team_name", ""),
            "player_name": f"{meta.get('first_name','')} {meta.get('last_name','')}".strip(),
            "position":   meta.get("position", ""),
        })

    # Playoff bracket / final standings
    bracket = requests.get(f"{BASE}/league/{SLEEPER_LEAGUE_ID}/winners_bracket", timeout=10).json()
    finish_map = {}
    if bracket:
        # find champ (w=1 in final round), runner-up, 3rd/4th
        rounds = defaultdict(list)
        for m in bracket:
            rounds[m.get("r", 0)].append(m)
        final_round = max(rounds.keys())
        for m in rounds[final_round]:
            if m.get("w"):
                wrid = str(m["w"])
                finish_map[roster_index.get(wrid, {}).get("owner_id", "")] = 1
            if m.get("l"):
                lrid = str(m["l"])
                finish_map[roster_index.get(lrid, {}).get("owner_id", "")] = 2
        if final_round > 1:
            for m in rounds[final_round - 1]:
                if m.get("l") and m.get("p") == 3:
                    lrid = str(m["l"])
                    finish_map[roster_index.get(lrid, {}).get("owner_id", "")] = 3

    for t in teams_out:
        t["final_standing"] = finish_map.get(t["owner_id"], None)

    # ── GM Score components from sleeper_data.json ───────────────────────────
    # sleeper_data.json is generated by fetchSleeperData.py and contains the
    # full transaction/matchup/player data needed for GM score computation.
    gm_scores = {}
    try:
        with open("/tmp/sleeper_data.json") as f:
            sd = json.load(f)

        scoring = sd.get("scoring_settings", {})
        players = sd.get("players", {})
        player_weekly_pts = sd.get("player_weekly_pts", {})

        # ── Draft VOE ──
        draft_picks_sd = sd.get("draft_picks", [])
        # Round medians
        from collections import defaultdict as _dd
        round_pts = _dd(list)
        for p in draft_picks_sd:
            pid = p["player_id"]
            sp = sum(player_weekly_pts.get(pid, {}).values())
            round_pts[p["round"]].append(sp)
        round_med = {r: sorted(v)[len(v)//2] for r, v in round_pts.items() if v}

        draft_voe_by_rid = _dd(float)
        for p in draft_picks_sd:
            pid = p["player_id"]
            sp = sum(player_weekly_pts.get(pid, {}).values())
            voe = sp - round_med.get(p["round"], 0)
            draft_voe_by_rid[str(p["roster_id"])] += voe

        # ── Waiver net (position-adjusted) ──
        waivers_sd = sd.get("waivers", [])
        pos_pts_by_pickup = _dd(list)  # position -> [received_pts]

        def pts_from_week(pid, from_wk):
            wks = player_weekly_pts.get(str(pid), {})
            return sum(v for k, v in wks.items() if int(k) >= from_wk)

        # First pass: collect all pickup pts by position
        for txn in waivers_sd:
            wk = int(txn["week"])
            for rid, pids in txn.get("adds", {}).items():
                for pid in (pids if isinstance(pids, list) else [pids]):
                    pid = str(pid)
                    pos = players.get(pid, {}).get("position", "UNK")
                    if pos not in ("K", "DEF", "UNK"):
                        rp = pts_from_week(pid, wk)
                        pos_pts_by_pickup[pos].append(rp)

        pos_med = {pos: sorted(v)[len(v)//2] for pos, v in pos_pts_by_pickup.items() if v}

        waiver_net_by_rid = _dd(list)
        for txn in waivers_sd:
            wk = int(txn["week"])
            for rid, pids in txn.get("adds", {}).items():
                for pid in (pids if isinstance(pids, list) else [pids]):
                    pid = str(pid)
                    pos = players.get(pid, {}).get("position", "UNK")
                    rp = pts_from_week(pid, wk)
                    adj = rp - pos_med.get(pos, 0)
                    waiver_net_by_rid[str(rid)].append(adj)

        waiver_avg_by_rid = {rid: sum(v)/len(v) for rid, v in waiver_net_by_rid.items() if v}

        # ── Trade net ──
        trades_sd = sd.get("trades", [])
        trade_net_by_rid = _dd(float)
        for txn in trades_sd:
            wk = int(txn["week"])
            for rid, pids in txn.get("adds", {}).items():
                rec = sum(pts_from_week(p, wk) for p in (pids if isinstance(pids, list) else [pids]))
                trade_net_by_rid[str(rid)] += rec
            for rid, pids in txn.get("drops", {}).items():
                gave = sum(pts_from_week(p, wk) for p in (pids if isinstance(pids, list) else [pids]))
                trade_net_by_rid[str(rid)] -= gave

        # ── Lineup efficiency (sit/start accuracy, weeks 1-14) ──
        # Need roster_positions to build optimal lineup
        roster_pos = sd.get("roster_positions", [])
        starter_slots = [p for p in roster_pos if p not in ("BN", "IR")]
        n_qb = starter_slots.count("QB"); n_rb = starter_slots.count("RB")
        n_wr = starter_slots.count("WR"); n_te = starter_slots.count("TE")
        n_k  = starter_slots.count("K");  n_def= starter_slots.count("DEF")
        n_flex = starter_slots.count("FLEX")
        flex_pos = {"RB","WR","TE"}

        def optimal_pts(pp_dict):
            rows = sorted(
                [(pid, float(pts), players.get(str(pid), {}).get("position","UNK"))
                 for pid, pts in pp_dict.items() if pid != "BYE"],
                key=lambda x: -x[1]
            )
            used = set(); total = 0
            def pick(positions, n):
                nonlocal total
                cnt = 0
                for pid, pts, pos in rows:
                    if pid not in used and pos in positions:
                        used.add(pid); total += pts; cnt += 1
                        if cnt == n: break
            pick({"QB"},n_qb); pick({"RB"},n_rb); pick({"WR"},n_wr)
            pick({"TE"},n_te); pick(flex_pos,n_flex); pick({"K"},n_k); pick({"DEF"},n_def)
            return total

        eff_by_rid = _dd(list)
        matchups_sd = sd.get("matchups", {})
        for wk_str, wk_data in matchups_sd.items():
            if int(wk_str) > 14: continue
            for m in wk_data:
                pp = m.get("players_points", {})
                starters = m.get("starters", [])
                if not starters or not pp: continue
                actual = sum(float(pp.get(p, 0)) for p in starters)
                opt = optimal_pts(pp)
                if opt > 0:
                    eff_by_rid[str(m["roster_id"])].append(actual / opt)

        eff_avg_by_rid = {rid: sum(v)/len(v) for rid, v in eff_by_rid.items() if v}

        # ── Map roster_id -> owner_id and build gm_scores ──
        rid_to_owner = {rid: info["owner_id"] for rid, info in roster_index.items()}

        raw_components = {}
        for rid, oid in rid_to_owner.items():
            raw_components[oid] = {
                "draft_voe":  round(draft_voe_by_rid.get(rid, 0), 2),
                "waiver_net": round(waiver_avg_by_rid.get(rid, 0), 3),
                "trade_net":  round(trade_net_by_rid.get(rid, 0), 2),
                "lineup_eff": round(eff_avg_by_rid.get(rid, 0), 4),
            }

        # Z-score each component across the league
        def zscores(vals):
            mu = sum(vals)/len(vals); sd_ = (sum((v-mu)**2 for v in vals)/len(vals))**0.5 + 1
            return [(v-mu)/sd_ for v in vals]

        owners_order = list(raw_components.keys())
        dv = zscores([raw_components[o]["draft_voe"]  for o in owners_order])
        wn = zscores([raw_components[o]["waiver_net"] for o in owners_order])
        tn = zscores([raw_components[o]["trade_net"]  for o in owners_order])
        le = zscores([raw_components[o]["lineup_eff"] for o in owners_order])

        for i, oid in enumerate(owners_order):
            raw_components[oid].update({
                "z_draft":  round(dv[i], 3),
                "z_waiver": round(wn[i], 3),
                "z_trade":  round(tn[i], 3),
                "z_lineup": round(le[i], 3),
                "gm_score": round(dv[i]+wn[i]+tn[i]+le[i], 3),
            })
            gm_scores[oid] = raw_components[oid]

        print(f"  GM scores computed for {len(gm_scores)} owners")
    except Exception as e:
        print(f"  GM score computation skipped: {e}")

    return {
        "source":   "sleeper",
        "year":     2025,
        "teams":    teams_out,
        "matchups": matchups_out,
        "draft":    draft_out,
        "reg_season_weeks": reg_weeks,
        "playoff_teams":    league_info.get("settings", {}).get("playoff_teams", 4),
        "gm_scores": gm_scores,
    }

# ── Main ────────────────────────────────────────────────────────────────────
print("Fetching all-years Room 40 data...")
all_years = []

print("ESPN seasons:")
for yr in ESPN_YEARS:
    try:
        all_years.append(fetch_espn_year(yr))
    except Exception as e:
        print(f"  ERROR {yr}: {e}")

print("Sleeper seasons:")
try:
    all_years.append(fetch_sleeper_year())
except Exception as e:
    print(f"  ERROR Sleeper: {e}")

output = {
    "owner_map": {oid: info["real"] for oid, info in OWNER_MAP.items()},
    "seasons":   all_years,
}

with open("/tmp/room40_all_years.json", "w") as f:
    json.dump(output, f)

print(f"DONE — {len(all_years)} seasons saved to /tmp/room40_all_years.json")
