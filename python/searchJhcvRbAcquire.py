import json, itertools

with open('/tmp/full_league_team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/full_league_weekly_proj.json') as f:
    weekly = json.load(f)
weekly = {str(k): v for k,v in weekly.items()}
with open('/tmp/all_league_players_full.json') as f:
    players = json.load(f)
with open('/tmp/full_league_baseline.json') as f:
    baseline = json.load(f)

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')
def is_skill(pid): return pos_of(pid) in ('QB','RB','WR','TE')

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

JHCV = 'jhcv'
PROTECTED_NAMES = {'Lamar Jackson', 'Emeka Egbuka'}
jhcv_candidates = [p for p in team_players[JHCV] if is_skill(p) and name_of(p) not in PROTECTED_NAMES]
print("jhcv tradeable assets:", [name_of(p) for p in jhcv_candidates])

other_teams = [t for t in team_players if t != JHCV]

results = []
for OTHER in other_teams:
    other_candidates = [p for p in team_players[OTHER] if is_skill(p)]
    # 1-for-1
    setsJ = [(p,) for p in jhcv_candidates]
    setsO = [(p,) for p in other_candidates]
    # 2-for-1 and 1-for-2 too
    setsJ2 = list(itertools.combinations(jhcv_candidates, 2))
    setsO2 = list(itertools.combinations(other_candidates, 2))

    combos = []
    for sj in setsJ: 
        for so in setsO: combos.append((sj,so))
    for sj in setsJ:
        for so in setsO2: combos.append((sj,so))
    for sj in setsJ2:
        for so in setsO: combos.append((sj,so))

    for sj, so in combos:
        newJ = [p for p in team_players[JHCV] if p not in sj] + list(so)
        newO = [p for p in team_players[OTHER] if p not in so] + list(sj)
        totJ = optimal_lineup_points(newJ)
        totO = optimal_lineup_points(newO)
        dJ = totJ - baseline[JHCV]
        dO = totO - baseline[OTHER]
        if dJ > 0.1 and dO > 0.1:
            incoming_rb = any(pos_of(p)=='RB' for p in so)
            if incoming_rb:
                results.append({'other':OTHER,'jhcv_sends':list(sj),'other_sends':list(so),'dJ':dJ,'dO':dO})
    print(f"{OTHER}: done, {len([r for r in results if r['other']==OTHER])} RB-acquiring wins so far", flush=True)

results.sort(key=lambda r: -(r['dJ']+r['dO']))
print(f"\n=== Total bilateral trades where jhcv acquires an RB and BOTH teams net-positive: {len(results)} ===")
for r in results[:30]:
    print(f"  jhcv sends {[name_of(p) for p in r['jhcv_sends']]} -> {r['other']}  | receives {[name_of(p) for p in r['other_sends']]}"
          f"  | jhcv +{r['dJ']:.1f}  {r['other']} +{r['dO']:.1f}")

with open('/tmp/jhcv_rb_acquire_bilateral.json','w') as f:
    json.dump(results, f, indent=2)
