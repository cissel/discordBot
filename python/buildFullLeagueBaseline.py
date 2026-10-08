import json

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)
with open('/tmp/full_league_weekly_proj.json') as f:
    weekly = json.load(f)  # week(str-ish int key) -> {pid: pts}
with open('/tmp/all_league_players_full.json') as f:
    players = json.load(f)

# weekly keys might be int (from json dump of dict with int keys -> becomes str keys in json)
weekly = {str(k): v for k,v in weekly.items()}

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

team_players = {r['display_name']: [str(p) for p in r['players']] for r in rosters}

def optimal_lineup_points(roster_pids):
    total = 0.0
    for wk_str, wk_data in weekly.items():
        avail = []
        for pid in roster_pids:
            pts = wk_data.get(pid)
            if pts is None: continue
            avail.append((pid, pos_of(pid), pts))
        qbs = sorted([a for a in avail if a[1]=='QB'], key=lambda x:-x[2])
        rbs = sorted([a for a in avail if a[1]=='RB'], key=lambda x:-x[2])
        wrs = sorted([a for a in avail if a[1]=='WR'], key=lambda x:-x[2])
        tes = sorted([a for a in avail if a[1]=='TE'], key=lambda x:-x[2])
        ks  = sorted([a for a in avail if a[1]=='K'], key=lambda x:-x[2])
        defs= sorted([a for a in avail if a[1]=='DEF'], key=lambda x:-x[2])
        used = set(); wk_total = 0.0
        def take(lst, n):
            nonlocal wk_total
            picked = []
            for a in lst:
                if len(picked) >= n: break
                if a[0] in used: continue
                picked.append(a); used.add(a[0]); wk_total += a[2]
            return picked
        take(qbs,1); take(rbs,2); take(wrs,2); take(tes,1); take(ks,1); take(defs,1)
        flex_pool = sorted([a for a in (rbs+wrs+tes) if a[0] not in used], key=lambda x:-x[2])
        take(flex_pool,1)
        total += wk_total
    return total

baseline = {t: optimal_lineup_points(pids) for t,pids in team_players.items()}
print("=== Baseline optimal ROS (weeks 3-17) for all 12 teams ===")
for t,v in sorted(baseline.items(), key=lambda x:-x[1]):
    print(f"  {t:16s}: {v:8.1f}")

with open('/tmp/full_league_team_players.json','w') as f:
    json.dump(team_players, f)
with open('/tmp/full_league_baseline.json','w') as f:
    json.dump(baseline, f)

# marginal value per player on their own team
marginal = {}
for team, pids in team_players.items():
    for pid in pids:
        reduced = [p for p in pids if p != pid]
        marginal[pid] = baseline[team] - optimal_lineup_points(reduced)

with open('/tmp/full_league_marginal.json','w') as f:
    json.dump(marginal, f)

print("\n=== All RBs leaguewide, sorted by marginal value (low marginal = tradeable RB depth) ===")
rb_rows = []
for team, pids in team_players.items():
    for pid in pids:
        if pos_of(pid) == 'RB':
            rb_rows.append((team, pid, marginal[pid]))
rb_rows.sort(key=lambda x: x[2])
for team, pid, m in rb_rows:
    print(f"  {team:16s} {name_of(pid):24s} marginal={m:7.1f}")
