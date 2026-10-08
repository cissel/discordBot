import json

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)  # week(str) -> {pid: pts}
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

TARGET = {'jhcv','CountDoobie','LeChuckski'}
team_players = {}
for r in rosters:
    if r['display_name'] in TARGET:
        team_players[r['display_name']] = [str(p) for p in r['players']]

def pos_of(pid):
    return players.get(pid, {}).get('position','')

def optimal_lineup_points(roster_pids, weekly):
    """Roster: QB, RB, RB, WR, WR, TE, FLEX(RB/WR/TE), K, DEF. Returns total ROS points (weeks 3-17)."""
    total = 0.0
    weekbyweek = {}
    for wk_str, wk_data in weekly.items():
        avail = []
        for pid in roster_pids:
            pts = wk_data.get(pid)
            if pts is None:
                continue
            avail.append((pid, pos_of(pid), pts))
        # Fill QB
        qbs = sorted([a for a in avail if a[1]=='QB'], key=lambda x:-x[2])
        rbs = sorted([a for a in avail if a[1]=='RB'], key=lambda x:-x[2])
        wrs = sorted([a for a in avail if a[1]=='WR'], key=lambda x:-x[2])
        tes = sorted([a for a in avail if a[1]=='TE'], key=lambda x:-x[2])
        ks  = sorted([a for a in avail if a[1]=='K'], key=lambda x:-x[2])
        defs= sorted([a for a in avail if a[1]=='DEF'], key=lambda x:-x[2])

        used = set()
        wk_total = 0.0
        def take(lst, n):
            nonlocal wk_total
            picked = []
            for a in lst:
                if len(picked) >= n:
                    break
                if a[0] in used:
                    continue
                picked.append(a)
                used.add(a[0])
                wk_total += a[2]
            return picked

        take(qbs, 1)
        take(rbs, 2)
        take(wrs, 2)
        take(tes, 1)
        take(ks, 1)
        take(defs, 1)
        # FLEX: best remaining RB/WR/TE
        flex_pool = sorted([a for a in (rbs+wrs+tes) if a[0] not in used], key=lambda x:-x[2])
        take(flex_pool, 1)

        weekbyweek[wk_str] = wk_total
        total += wk_total
    return total, weekbyweek

print("=== BASELINE (current rosters) optimal-lineup ROS points, weeks 3-17 ===")
baseline = {}
for team, pids in team_players.items():
    total, wbw = optimal_lineup_points(pids, weekly)
    baseline[team] = total
    print(f"{team:12s}: {total:8.1f}")

with open('/tmp/team_players.json','w') as f:
    json.dump(team_players, f)
with open('/tmp/baseline_optimal.json','w') as f:
    json.dump(baseline, f)
