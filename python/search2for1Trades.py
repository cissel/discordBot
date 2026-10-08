import json, itertools

with open('/tmp/team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

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

teams = ['jhcv','LeChuckski','CountDoobie']
baseline = {t: optimal_lineup_points(team_players[t]) for t in teams}
print("Baseline:", {t: round(v,1) for t,v in baseline.items()})

def is_skill(pid): return pos_of(pid) in ('QB','RB','WR','TE')
candidates = {t: [p for p in team_players[t] if is_skill(p)] for t in teams}

# 2-for-1 trades: team A sends 2 players, team B sends 1, between each pair
results = []
for A, B in itertools.permutations(teams, 2):
    for pA1, pA2 in itertools.combinations(candidates[A], 2):
        for pB in candidates[B]:
            newA = [p for p in team_players[A] if p not in (pA1,pA2)] + [pB]
            newB = [p for p in team_players[B] if p != pB] + [pA1, pA2]
            totA = optimal_lineup_points(newA)
            totB = optimal_lineup_points(newB)
            dA = totA - baseline[A]
            dB = totB - baseline[B]
            if dA > 0.5 and dB > 0.5:
                results.append({'A':A,'gives':[pA1,pA2],'B':B,'gives_b':pB,'dA':dA,'dB':dB})

results.sort(key=lambda r: -(r['dA']+r['dB']))
print(f"\n2-for-1 trades where both improve: {len(results)}")
for r in results[:20]:
    print(f"  {r['A']} sends {name_of(r['gives'][0])}+{name_of(r['gives'][1])} -> {r['B']}  | {r['B']} sends {name_of(r['gives_b'])} -> {r['A']}")
    print(f"    Gains: {r['A']} +{r['dA']:.1f}  {r['B']} +{r['dB']:.1f}")

with open('/tmp/two_for_one_results.json','w') as f:
    json.dump(results, f, indent=2)
