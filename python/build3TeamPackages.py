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
    raise ValueError(f"{nm} not found on {team}")

# Candidate composite 3-team packages built from the disjoint 2-for-2 pairwise wins found earlier.
# Package 1: (LeChuckski<->CountDoobie #3) + (jhcv<->LeChuckski #4)
packages = {
 "PKG_A (LC<->CD Nabers/Jones-Goedert/Warren + jhcv<->LC Egbuka/Jackson-Goff/Watson)": {
    'jhcv':      {'out': ['Emeka Egbuka','Lamar Jackson'], 'in': ['Jared Goff','Christian Watson']},
    'LeChuckski':{'out': ['Jared Goff','Christian Watson','Malik Nabers','Aaron Jones'],
                  'in':  ['Emeka Egbuka','Lamar Jackson','Dallas Goedert','Jaylen Warren']},
    'CountDoobie':{'out': ['Dallas Goedert','Jaylen Warren'], 'in': ['Malik Nabers','Aaron Jones']},
 },
 "PKG_B (jhcv<->LC Egbuka/Jackson-Goff/Watson + jhcv<->CD Barner/Williams-Golden/Goedert)": {
    'jhcv':      {'out': ['Emeka Egbuka','Lamar Jackson','AJ Barner','Jameson Williams'],
                  'in':  ['Jared Goff','Christian Watson','Matthew Golden','Dallas Goedert']},
    'LeChuckski':{'out': ['Jared Goff','Christian Watson'], 'in': ['Emeka Egbuka','Lamar Jackson']},
    'CountDoobie':{'out': ['Matthew Golden','Dallas Goedert'], 'in': ['AJ Barner','Jameson Williams']},
 },
 "PKG_C (jhcv<->LC Egbuka/Jackson-Goff/Watson + jhcv<->CD Hampton/Sutton-Golden/Warren)": {
    'jhcv':      {'out': ['Emeka Egbuka','Lamar Jackson','Omarion Hampton','Courtland Sutton'],
                  'in':  ['Jared Goff','Christian Watson','Matthew Golden','Jaylen Warren']},
    'LeChuckski':{'out': ['Jared Goff','Christian Watson'], 'in': ['Emeka Egbuka','Lamar Jackson']},
    'CountDoobie':{'out': ['Matthew Golden','Jaylen Warren'], 'in': ['Omarion Hampton','Courtland Sutton']},
 },
 "PKG_D (jhcv<->LC Egbuka/Jackson-Goff/Watson + jhcv<->CD Barner/Hampton-Goedert/Warren)": {
    'jhcv':      {'out': ['Emeka Egbuka','Lamar Jackson','AJ Barner','Omarion Hampton'],
                  'in':  ['Jared Goff','Christian Watson','Dallas Goedert','Jaylen Warren']},
    'LeChuckski':{'out': ['Jared Goff','Christian Watson'], 'in': ['Emeka Egbuka','Lamar Jackson']},
    'CountDoobie':{'out': ['Dallas Goedert','Jaylen Warren'], 'in': ['AJ Barner','Omarion Hampton']},
 },
}

for label, pkg in packages.items():
    print(f"\n=== {label} ===")
    new_rosters = {}
    for t in teams:
        pids = set(team_players[t])
        spec = pkg.get(t, {'out':[],'in':[]})
        for nm in spec['out']:
            pid = name_to_pid(nm, t)
            pids.discard(pid)
        new_rosters[t] = pids
    # now add "in" players (must resolve pid from originating team's original roster)
    for t in teams:
        spec = pkg.get(t, {'out':[],'in':[]})
        for nm in spec['in']:
            # find which OTHER team originally had this player
            src_pid = None
            for t2 in teams:
                if t2 == t: continue
                for pid in team_players[t2]:
                    if name_of(pid) == nm:
                        src_pid = pid
                        break
                if src_pid: break
            if src_pid is None:
                raise ValueError(f"can't resolve incoming player {nm} for {t}")
            new_rosters[t].add(src_pid)

    all_ok = True
    for t in teams:
        new_total = optimal_lineup_points(list(new_rosters[t]))
        delta = new_total - baseline[t]
        flag = "OK" if delta > 0 else "**WORSE**"
        if delta <= 0: all_ok = False
        print(f"  {t:12s}: baseline={baseline[t]:7.1f} -> new={new_total:7.1f}  delta={delta:+6.1f}  {flag}")
    print(f"  ALL THREE IMPROVE: {all_ok}")
