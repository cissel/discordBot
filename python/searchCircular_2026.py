import json, itertools
from optimalLineupLib import *

teams = ['jhcv', 'LeChuckski', 'CountDoobie']
baseline = {t: optimal_lineup_points(team_players[t]) for t in teams}
print('Baseline:', {t: round(v,1) for t,v in baseline.items()})

def is_skill(pid): return pos_of(pid) in ('QB','RB','WR','TE')

# Protect each team's single highest-marginal skill asset from being traded away
marginal = {}
for t in teams:
    for pid in team_players[t]:
        marginal[pid] = marginal_value(pid, team_players[t])

anchor = {}
for t in teams:
    skill_pids = [p for p in team_players[t] if is_skill(p)]
    anchor[t] = max(skill_pids, key=lambda p: marginal[p])
    print(f"{t} anchor (protected): {name_of(anchor[t])} marginal={marginal[anchor[t]]:.1f}")

candidates = {t: [p for p in team_players[t] if is_skill(p) and p != anchor[t]] for t in teams}

results = []
A, B, C = 'jhcv', 'LeChuckski', 'CountDoobie'
a_sets = list(itertools.combinations(candidates[A], 1)) + list(itertools.combinations(candidates[A], 2))
b_sets = list(itertools.combinations(candidates[B], 1)) + list(itertools.combinations(candidates[B], 2))
c_sets = list(itertools.combinations(candidates[C], 1)) + list(itertools.combinations(candidates[C], 2))
print(f"Circular A->B->C->A: {len(a_sets)} x {len(b_sets)} x {len(c_sets)} = {len(a_sets)*len(b_sets)*len(c_sets)}")

count = 0
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
                results.append({'A_sends':list(sa),'B_sends':list(sb),'C_sends':list(sc),'dA':dA,'dB':dB,'dC':dC})
    count += 1
    if count % 10 == 0:
        print(f"  progress: {count}/{len(a_sets)} A-sets done, {len(results)} hits so far", flush=True)

results.sort(key=lambda r: -(r['dA']+r['dB']+r['dC']))
print(f"\nTotal circular (anchor-protected) trades where ALL THREE improve: {len(results)}")
for r in results[:15]:
    print(f"\n  {A} sends {[name_of(p) for p in r['A_sends']]} -> {B}")
    print(f"  {B} sends {[name_of(p) for p in r['B_sends']]} -> {C}")
    print(f"  {C} sends {[name_of(p) for p in r['C_sends']]} -> {A}")
    print(f"  Gains: {A} +{r['dA']:.1f}  {B} +{r['dB']:.1f}  {C} +{r['dC']:.1f}  TOTAL +{r['dA']+r['dB']+r['dC']:.1f}")

with open('/tmp/circular_2026_results.json','w') as f:
    json.dump(results, f, indent=2)
