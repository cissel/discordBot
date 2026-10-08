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

def name_to_pid(nm, team):
    for pid in team_players[team]:
        if name_of(pid) == nm:
            return pid
    raise ValueError(nm)

# Best genuine 3-way cycle candidate (no overlapping players), jhcv keeps Jackson & Egbuka:
# Leg1 (jhcv -> CountDoobie): Courtland Sutton  for  DK Metcalf
# Leg2 (LeChuckski -> CountDoobie): Malik Nabers + Aaron Jones  for  Dallas Goedert + Jaylen Warren
# This routes CountDoobie's incoming (Sutton, Nabers, Jones) and outgoing (Metcalf, Goedert, Warren)
# but doesn't touch LeChuckski<->jhcv directly. Structure it as true 3-team: have CountDoobie's Goedert
# go to jhcv instead of LeChuckski, and route Warren to LeChuckski, to make all three exchange with each other.

# Try explicit 3-way: 
#  jhcv sends Courtland Sutton -> CountDoobie
#  CountDoobie sends Dallas Goedert -> LeChuckski
#  LeChuckski sends Malik Nabers + Aaron Jones -> CountDoobie ... but then CountDoobie has 2 outs/2 ins across parties, jhcv gets nothing back directly

# Simpler: verify the bundled (non-overlapping) two-bilateral combo actually works as an all-3-way trade
# where CountDoobie is the hub, since Sleeper supports 3-team trades processed as simultaneous transactions.
sutton = name_to_pid('Courtland Sutton','jhcv')
metcalf = name_to_pid('DK Metcalf','CountDoobie')
nabers = name_to_pid('Malik Nabers','LeChuckski')
jones = name_to_pid('Aaron Jones','LeChuckski')
goedert = name_to_pid('Dallas Goedert','CountDoobie')
warren = name_to_pid('Jaylen Warren','CountDoobie')

new_jhcv = [p for p in team_players['jhcv'] if p != sutton] + [metcalf]
new_lechuck = [p for p in team_players['LeChuckski'] if p not in (nabers,jones)] + [goedert,warren]
new_countd = [p for p in team_players['CountDoobie'] if p not in (metcalf,goedert,warren)] + [sutton,nabers,jones]

for t,new in [('jhcv',new_jhcv), ('LeChuckski',new_lechuck), ('CountDoobie',new_countd)]:
    tot = optimal_lineup_points(new)
    print(f"{t}: baseline={baseline[t]:.1f} -> new={tot:.1f}  delta={tot-baseline[t]:+.1f}")

print("\nRoster check (counts should match original):")
for t,new in [('jhcv',new_jhcv), ('LeChuckski',new_lechuck), ('CountDoobie',new_countd)]:
    print(t, len(new), 'orig', len(team_players[t]))
