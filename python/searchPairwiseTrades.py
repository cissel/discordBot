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

teams = list(team_players.keys())
baseline = {t: optimal_lineup_points(team_players[t]) for t in teams}
print("Baseline:", {t: round(v,1) for t,v in baseline.items()})

# Restrict to skill positions (QB/RB/WR/TE) for trade candidates - K/DEF trades are rarely meaningful
def is_skill(pid):
    return pos_of(pid) in ('QB','RB','WR','TE')

candidates = {t: [p for p in team_players[t] if is_skill(p)] for t in teams}

# ---- Search 1: pairwise 1-for-1 trades between every pair of teams ----
print("\n=== Pairwise 1-for-1 trades where BOTH teams improve ===")
pairwise_wins = []
for t1, t2 in itertools.combinations(teams, 2):
    for p1 in candidates[t1]:
        for p2 in candidates[t2]:
            new_r1 = [p for p in team_players[t1] if p != p1] + [p2]
            new_r2 = [p for p in team_players[t2] if p != p2] + [p1]
            new_t1 = optimal_lineup_points(new_r1)
            new_t2 = optimal_lineup_points(new_r2)
            d1 = new_t1 - baseline[t1]
            d2 = new_t2 - baseline[t2]
            if d1 > 0.5 and d2 > 0.5:
                pairwise_wins.append((t1,p1,t2,p2,d1,d2))

pairwise_wins.sort(key=lambda x: -(x[4]+x[5]))
for t1,p1,t2,p2,d1,d2 in pairwise_wins[:40]:
    print(f"  {t1} sends {name_of(p1):22s} <-> {t2} sends {name_of(p2):22s}  | {t1} +{d1:6.1f}  {t2} +{d2:6.1f}")

with open('/tmp/pairwise_wins.json','w') as f:
    json.dump(pairwise_wins, f)
