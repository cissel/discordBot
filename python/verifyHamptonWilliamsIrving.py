import json

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

def name_to_pid(nm, team):
    for pid in team_players[team]:
        if name_of(pid) == nm:
            return pid
    raise ValueError(nm)

hampton = name_to_pid('Omarion Hampton','jhcv')
williams = name_to_pid('Jameson Williams','jhcv')
irving = name_to_pid('Bucky Irving','JamesAshcraft')

new_jhcv = [p for p in team_players['jhcv'] if p not in (hampton,williams)] + [irving]
new_james = [p for p in team_players['JamesAshcraft'] if p != irving] + [hampton,williams]

tot_j = optimal_lineup_points(new_jhcv)
tot_ja = optimal_lineup_points(new_james)
print(f"jhcv: baseline={baseline['jhcv']:.1f} -> new={tot_j:.1f}  delta={tot_j-baseline['jhcv']:+.1f}")
print(f"JamesAshcraft: baseline={baseline['JamesAshcraft']:.1f} -> new={tot_ja:.1f}  delta={tot_ja-baseline['JamesAshcraft']:+.1f}")
print(f"jhcv roster size: {len(new_jhcv)} (orig {len(team_players['jhcv'])})")
print(f"JamesAshcraft roster size: {len(new_james)} (orig {len(team_players['JamesAshcraft'])})")
