import json

with open('/tmp/team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

def pos_of(pid):
    return players.get(pid, {}).get('position','')
def name_of(pid):
    return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

def optimal_lineup_used(roster_pids, weekly):
    """Return total pts, and per-player used-points sum + per-player total-avail sum (so we can see bench waste)."""
    total = 0.0
    used_by_player = {pid: 0.0 for pid in roster_pids}
    avail_by_player = {pid: 0.0 for pid in roster_pids}
    slot_fill_gaps = {'QB':0,'RB':0,'WR':0,'TE':0,'FLEX':0,'K':0,'DEF':0}  # count of weeks a slot went unfilled
    for wk_str, wk_data in weekly.items():
        avail = []
        for pid in roster_pids:
            pts = wk_data.get(pid)
            if pts is None:
                continue
            avail_by_player[pid] += pts
            avail.append((pid, pos_of(pid), pts))
        qbs = sorted([a for a in avail if a[1]=='QB'], key=lambda x:-x[2])
        rbs = sorted([a for a in avail if a[1]=='RB'], key=lambda x:-x[2])
        wrs = sorted([a for a in avail if a[1]=='WR'], key=lambda x:-x[2])
        tes = sorted([a for a in avail if a[1]=='TE'], key=lambda x:-x[2])
        ks  = sorted([a for a in avail if a[1]=='K'], key=lambda x:-x[2])
        defs= sorted([a for a in avail if a[1]=='DEF'], key=lambda x:-x[2])
        used = set()
        wk_total = 0.0
        def take(lst, n, slotname):
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
                used_by_player[a[0]] += a[2]
            if len(picked) < n:
                slot_fill_gaps[slotname] += (n - len(picked))
            return picked
        take(qbs,1,'QB'); take(rbs,2,'RB'); take(wrs,2,'WR'); take(tes,1,'TE')
        take(ks,1,'K'); take(defs,1,'DEF')
        flex_pool = sorted([a for a in (rbs+wrs+tes) if a[0] not in used], key=lambda x:-x[2])
        take(flex_pool,1,'FLEX')
        total += wk_total
    return total, used_by_player, avail_by_player, slot_fill_gaps

for team, pids in team_players.items():
    total, used, avail, gaps = optimal_lineup_used(pids, weekly)
    print(f"\n=== {team} === optimal total={total:.1f}  unfilled-slot-weeks={gaps}")
    for pid in sorted(pids, key=lambda p: -avail[p]):
        wasted = avail[pid] - used[pid]
        print(f"  {pos_of(pid):3s} {name_of(pid):24s} avail={avail[pid]:7.1f} used={used[pid]:7.1f} WASTED={wasted:7.1f}")
