import json, itertools

with open('/tmp/team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)
with open('/tmp/marginal_values.json') as f:
    marginal = json.load(f)

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

# Exclude each team's single highest-marginal ("anchor") asset from being traded away
anchor = {}
for t in teams:
    skill_pids = [p for p in team_players[t] if is_skill(p)]
    anchor[t] = max(skill_pids, key=lambda p: marginal[p])
    print(f"{t} anchor (protected): {name_of(anchor[t])} marginal={marginal[anchor[t]]:.1f}")

candidates = {t: [p for p in team_players[t] if is_skill(p) and p != anchor[t]] for t in teams}

# Triangular trade: A -> B (1 or 2 players), B -> C (1 or 2), C -> A (1 or 2)
results = []
for order in itertools.permutations(teams):
    A,B,C = order
    a_sets = list(itertools.combinations(candidates[A],1)) + list(itertools.combinations(candidates[A],2))
    b_sets = list(itertools.combinations(candidates[B],1)) + list(itertools.combinations(candidates[B],2))
    c_sets = list(itertools.combinations(candidates[C],1)) + list(itertools.combinations(candidates[C],2))
    print(f"order {order}: {len(a_sets)} x {len(b_sets)} x {len(c_sets)} = {len(a_sets)*len(b_sets)*len(c_sets)}", flush=True)
    for sa in a_sets:
        for sb in b_sets:
            for sc in c_sets:
                newA = [p for p in team_players[A] if p not in sa] + list(sc)
                newB = [p for p in team_players[B] if p not in sb] + list(sa)
                newC = [p for p in team_players[C] if p not in sc] + list(sb)
                totA = optimal_lineup_points(newA)
                totB = optimal_lineup_points(newB)
                totC = optimal_lineup_points(newC)
                dA = totA - baseline[A]; dB = totB - baseline[B]; dC = totC - baseline[C]
                if dA > 0.5 and dB > 0.5 and dC > 0.5:
                    results.append({'A':A,'B':B,'C':C,'A_sends':list(sa),'B_sends':list(sb),'C_sends':list(sc),
                                     'dA':dA,'dB':dB,'dC':dC})
    # only need to check each cyclic order once (not all 6 perms give distinct trades but cheap enough)
    if len(results) > 500:
        break

results.sort(key=lambda r: -(r['dA']+r['dB']+r['dC']))
print(f"\nTotal triangular (anchor-protected) trades where ALL THREE improve: {len(results)}")
seen = set()
shown = 0
for r in results:
    key = (tuple(sorted(r['A_sends'])), tuple(sorted(r['B_sends'])), tuple(sorted(r['C_sends'])))
    if key in seen: continue
    seen.add(key)
    print(f"\n  {r['A']} sends {[name_of(p) for p in r['A_sends']]} -> {r['B']}")
    print(f"  {r['B']} sends {[name_of(p) for p in r['B_sends']]} -> {r['C']}")
    print(f"  {r['C']} sends {[name_of(p) for p in r['C_sends']]} -> {r['A']}")
    print(f"  Gains: {r['A']} +{r['dA']:.1f}  {r['B']} +{r['dB']:.1f}  {r['C']} +{r['dC']:.1f}  TOTAL +{r['dA']+r['dB']+r['dC']:.1f}")
    shown += 1
    if shown >= 15:
        break

with open('/tmp/triangular_anchor_protected.json','w') as f:
    json.dump(results, f, indent=2)
