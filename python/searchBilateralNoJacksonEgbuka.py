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

PROTECTED = {'jhcv': {'Lamar Jackson', 'Emeka Egbuka'}}

candidates = {}
for t in teams:
    prot = PROTECTED.get(t, set())
    candidates[t] = [p for p in team_players[t] if is_skill(p) and name_of(p) not in prot]

print({t: len(c) for t,c in candidates.items()})

# Enumerate ALL bilateral trades (sizes up to 2-for-2) between each pair, both directions,
# keep every win (even marginal) so we can search combos.
def bilateral_wins(A, B):
    wins = []
    setsA = [()] + [(p,) for p in candidates[A]] + list(itertools.combinations(candidates[A],2))
    setsB = [()] + [(p,) for p in candidates[B]] + list(itertools.combinations(candidates[B],2))
    for sa in setsA:
        if len(sa)==0: continue
        for sb in setsB:
            if len(sb)==0: continue
            if len(sa) > 2 or len(sb) > 2: continue
            newA = [p for p in team_players[A] if p not in sa] + list(sb)
            newB = [p for p in team_players[B] if p not in sb] + list(sa)
            totA = optimal_lineup_points(newA)
            totB = optimal_lineup_points(newB)
            dA = totA - baseline[A]; dB = totB - baseline[B]
            if dA > 0.1 and dB > 0.1:
                wins.append({'A':A,'B':B,'A_sends':list(sa),'B_sends':list(sb),'dA':dA,'dB':dB})
    return wins

pairs = [('jhcv','LeChuckski'), ('jhcv','CountDoobie'), ('LeChuckski','CountDoobie')]
all_wins = {}
for A,B in pairs:
    w = bilateral_wins(A,B)
    all_wins[f"{A}-{B}"] = w
    print(f"\n=== {A}-{B}: {len(w)} winning bilateral sub-trades found ===", flush=True)
    for r in w:
        print(f"  {A} sends {[name_of(p) for p in r['A_sends']]} -> {B}  | {B} sends {[name_of(p) for p in r['B_sends']]} -> {A}"
              f"  | dA={r['dA']:.1f} dB={r['dB']:.1f}", flush=True)

with open('/tmp/bilateral_wins_no_jackson_egbuka.json','w') as f:
    json.dump(all_wins, f, indent=2)
print("saved bilateral_wins_no_jackson_egbuka.json")
