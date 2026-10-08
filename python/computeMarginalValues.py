import json, itertools

with open('/tmp/team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

def optimal_lineup_points(roster_pids, weekly):
    total = 0.0
    for wk_str, wk_data in weekly.items():
        avail = []
        for pid in roster_pids:
            pts = wk_data.get(pid)
            if pts is None:
                continue
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

baseline = {t: optimal_lineup_points(pids, weekly) for t,pids in team_players.items()}
print("Baseline:", {t: round(v,1) for t,v in baseline.items()})

# marginal value of each player to their own team = drop-out delta
marginal = {}
for team, pids in team_players.items():
    for pid in pids:
        reduced = [p for p in pids if p != pid]
        marginal[pid] = baseline[team] - optimal_lineup_points(reduced, weekly)

print("\n=== Marginal value (points added to own optimal lineup) by player ===")
for team, pids in team_players.items():
    print(f"\n{team}:")
    for pid in sorted(pids, key=lambda p: -marginal[p]):
        print(f"  {pos_of(pid):3s} {name_of(pid):24s} marginal={marginal[pid]:7.1f}")

with open('/tmp/marginal_values.json','w') as f:
    json.dump(marginal, f)
with open('/tmp/baseline_optimal2.json','w') as f:
    json.dump(baseline, f)
