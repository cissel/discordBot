import json

with open('/tmp/team_players.json') as f:
    team_players = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

def optimal_lineup_weekly(roster_pids):
    out = {}
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
        out[wk_str] = wk_total
    return out

def name_to_pid(nm, team):
    for pid in team_players[team]:
        if name_of(pid) == nm:
            return pid
    raise ValueError(nm)

jhcv_old = list(team_players['jhcv'])
egbuka = name_to_pid('Emeka Egbuka','jhcv')
jackson = name_to_pid('Lamar Jackson','jhcv')
barner = name_to_pid('AJ Barner','jhcv')
williams = name_to_pid('Jameson Williams','jhcv')
goff = name_to_pid('Jared Goff','LeChuckski')
watson = name_to_pid('Christian Watson','LeChuckski')
golden = name_to_pid('Matthew Golden','CountDoobie')
goedert = name_to_pid('Dallas Goedert','CountDoobie')

jhcv_new = [p for p in jhcv_old if p not in (egbuka,jackson,barner,williams)] + [goff,watson,golden,goedert]

old_wk = optimal_lineup_weekly(jhcv_old)
new_wk = optimal_lineup_weekly(jhcv_new)
print("week | old_optimal | new_optimal | delta")
for wk in sorted(old_wk, key=int):
    print(f"{wk:>4s} | {old_wk[wk]:8.2f} | {new_wk[wk]:8.2f} | {new_wk[wk]-old_wk[wk]:+6.2f}")
print(f"\nTOTAL old={sum(old_wk.values()):.1f} new={sum(new_wk.values()):.1f} delta={sum(new_wk.values())-sum(old_wk.values()):+.1f}")
