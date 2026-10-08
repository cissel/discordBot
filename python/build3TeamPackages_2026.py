import json, itertools
from optimalLineupLib import *

teams = ['jhcv', 'LeChuckski', 'CountDoobie']
baseline = {t: optimal_lineup_points(team_players[t]) for t in teams}
print('Baseline:', {t: round(v,1) for t,v in baseline.items()})

def name_to_pid(nm, team):
    for pid in team_players[team]:
        if name_of(pid) == nm:
            return pid
    raise ValueError(f"{nm} not found on {team}")

def apply_package(pkg):
    """pkg: {team: {'out': [names], 'in': [names]}}"""
    new_rosters = {}
    for t in teams:
        pids = set(team_players[t])
        spec = pkg.get(t, {'out': [], 'in': []})
        for nm in spec['out']:
            pid = name_to_pid(nm, t)
            pids.discard(pid)
        new_rosters[t] = pids
    for t in teams:
        spec = pkg.get(t, {'out': [], 'in': []})
        for nm in spec['in']:
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
    return new_rosters

packages = {
    "PKG_A: jhcv<->LC (Egbuka/Jackson-Goff/Watson) + jhcv<->CD (Barner/Williams-Golden/Goedert)": {
        'jhcv':       {'out': ['Emeka Egbuka','Lamar Jackson','AJ Barner','Jameson Williams'],
                        'in':  ['Jared Goff','Christian Watson','Matthew Golden','Dallas Goedert']},
        'LeChuckski': {'out': ['Jared Goff','Christian Watson'], 'in': ['Emeka Egbuka','Lamar Jackson']},
        'CountDoobie':{'out': ['Matthew Golden','Dallas Goedert'], 'in': ['AJ Barner','Jameson Williams']},
    },
    "PKG_B: jhcv<->LC (Egbuka/Jackson-Goff/Watson) + jhcv<->CD (Hampton/Sutton-Golden/Warren)": {
        'jhcv':       {'out': ['Emeka Egbuka','Lamar Jackson','Omarion Hampton','Courtland Sutton'],
                        'in':  ['Jared Goff','Christian Watson','Matthew Golden','Jaylen Warren']},
        'LeChuckski': {'out': ['Jared Goff','Christian Watson'], 'in': ['Emeka Egbuka','Lamar Jackson']},
        'CountDoobie':{'out': ['Matthew Golden','Jaylen Warren'], 'in': ['Omarion Hampton','Courtland Sutton']},
    },
    "PKG_C: jhcv<->LC (Barner/QJohnston-Diggs/Jones) + jhcv<->CD (Barner/Jameson-Golden/Goedert)  [invalid dup, skip check]": None,
    "PKG_D: jhcv<->LC (Barner/Bateman-Diggs/Jones) + LC<->CD (Nabers/Jones-Goedert/Warren)": {
        'jhcv':       {'out': ['AJ Barner','Rashod Bateman'], 'in': ['Stefon Diggs','Aaron Jones']},
        'LeChuckski': {'out': ['Stefon Diggs','Aaron Jones','Malik Nabers'],
                        'in':  ['AJ Barner','Rashod Bateman','Dallas Goedert']},
        'CountDoobie':{'out': ['Dallas Goedert'], 'in': ['Malik Nabers']},
    },
    "PKG_E: jhcv<->LC (Sutton-DKMetcalf via CD chain) + jhcv<->CD (Sutton-Metcalf) + LC<->CD (Nabers/Jones-Goedert/Warren)": {
        'jhcv':       {'out': ['Courtland Sutton'], 'in': ['DK Metcalf']},
        'CountDoobie':{'out': ['DK Metcalf','Dallas Goedert','Jaylen Warren'], 'in': ['Courtland Sutton','Malik Nabers','Aaron Jones']},
        'LeChuckski': {'out': ['Malik Nabers','Aaron Jones'], 'in': ['Dallas Goedert','Jaylen Warren']},
    },
}

for label, pkg in packages.items():
    if pkg is None:
        continue
    print(f"\n=== {label} ===")
    new_rosters = apply_package(pkg)
    all_ok = True
    deltas = {}
    for t in teams:
        new_total = optimal_lineup_points(list(new_rosters[t]))
        delta = new_total - baseline[t]
        deltas[t] = delta
        flag = "OK" if delta > 0 else "**WORSE**"
        if delta <= 0: all_ok = False
        print(f"  {t:12s}: baseline={baseline[t]:7.1f} -> new={new_total:7.1f}  delta={delta:+6.1f}  {flag}")
    print(f"  ALL THREE IMPROVE: {all_ok}  TOTAL_GAIN={sum(deltas.values()):+.1f}")
