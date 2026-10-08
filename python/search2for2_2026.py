import json, itertools
from optimalLineupLib import *

teams = ['jhcv', 'LeChuckski', 'CountDoobie']
baseline = {t: optimal_lineup_points(team_players[t]) for t in teams}
print('Baseline:', {t: round(v,1) for t,v in baseline.items()})

def is_skill(pid): return pos_of(pid) in ('QB','RB','WR','TE')

def pairwise_2for2(A, B):
    cand_A = [p for p in team_players[A] if is_skill(p)]
    cand_B = [p for p in team_players[B] if is_skill(p)]
    a_sets = list(itertools.combinations(cand_A, 1)) + list(itertools.combinations(cand_A, 2))
    b_sets = list(itertools.combinations(cand_B, 1)) + list(itertools.combinations(cand_B, 2))
    print(f'{A} vs {B}: {len(a_sets)} x {len(b_sets)} = {len(a_sets)*len(b_sets)} combos')
    results = []
    for sa in a_sets:
        newA_minus = [p for p in team_players[A] if p not in sa]
        for sb in b_sets:
            newA = newA_minus + list(sb)
            newB = [p for p in team_players[B] if p not in sb] + list(sa)
            totA = optimal_lineup_points(newA)
            totB = optimal_lineup_points(newB)
            dA = totA - baseline[A]
            dB = totB - baseline[B]
            if dA > 0.1 and dB > 0.1:
                results.append({'A':A,'B':B,'A_sends':list(sa),'B_sends':list(sb),'dA':dA,'dB':dB})
    return results

all_results = {}
for A,B in itertools.combinations(teams, 2):
    all_results[(A,B)] = pairwise_2for2(A,B)

with open('/tmp/pairwise_2for2_2026.json','w') as f:
    json.dump({f'{a}|{b}': v for (a,b),v in all_results.items()}, f)

for (A,B), res in all_results.items():
    res.sort(key=lambda r: -(r['dA']+r['dB']))
    print(f'\n=== {A} <-> {B}: {len(res)} mutually-improving 2-for-2 trades ===')
    for r in res[:10]:
        print(f"  {A} sends {[name_of(p) for p in r['A_sends']]} -> {B}  | {B} sends {[name_of(p) for p in r['B_sends']]} -> {A}  | d{A}={r['dA']:.1f} d{B}={r['dB']:.1f}")
