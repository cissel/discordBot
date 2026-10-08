import json
with open('/tmp/two_for_two_results.json') as f:
    results = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')

print(f"total 2-for-2 results: {len(results)}")
for r in results:
    print(f"{r['A']:12s} sends {name_of(r['gives'][0])+'+'+name_of(r['gives'][1]):45s} -> {r['B']:12s} | dA={r['dA']:.1f} dB={r['dB']:.1f}")
    print(f"{r['B']:12s} sends {name_of(r['gives_b'][0])+'+'+name_of(r['gives_b'][1]):45s} -> {r['A']}")
    print()
