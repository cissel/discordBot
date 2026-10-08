import requests, json

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)

all_pids = set()
for r in rosters:
    all_pids.update(str(p) for p in r['players'])

print(f'fetching full player pool for {len(all_pids)} unique players...', flush=True)
resp = requests.get('https://api.sleeper.app/v1/players/nfl', timeout=120)
players_raw = resp.json()
out = {}
for pid in all_pids:
    p = players_raw.get(str(pid), {})
    if p:
        out[str(pid)] = {
            'full_name': p.get('full_name') or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            'position': p.get('position',''),
            'team': p.get('team','FA'),
            'status': p.get('status',''),
            'injury_status': p.get('injury_status',''),
        }
with open('/tmp/all_league_players_full.json','w') as f:
    json.dump(out, f)
print(f'saved {len(out)} players', flush=True)
