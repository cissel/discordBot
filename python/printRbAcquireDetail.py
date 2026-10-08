import json

with open('/tmp/jhcv_rb_acquire_bilateral.json') as f:
    results = json.load(f)
with open('/tmp/all_league_players_full.json') as f:
    players = json.load(f)
with open('/tmp/full_league_marginal.json') as f:
    marginal = json.load(f)

def pos_of(pid): return players.get(pid, {}).get('position','')
def name_of(pid): return players.get(pid, {}).get('full_name', f'UNKNOWN_{pid}')
def team_of(pid): return players.get(pid, {}).get('team','FA')

results.sort(key=lambda r: -(r['dJ']+r['dO']))
print(f"Total: {len(results)}\n")
for r in results:
    sends = ", ".join(f"{name_of(p)}({pos_of(p)})" for p in r['jhcv_sends'])
    recv = ", ".join(f"{name_of(p)}({pos_of(p)},{team_of(p)},orig_marginal={marginal.get(p,0):.1f})" for p in r['other_sends'])
    print(f"jhcv sends [{sends}] -> {r['other']}")
    print(f"jhcv receives [{recv}]")
    print(f"  jhcv +{r['dJ']:.1f}   {r['other']} +{r['dO']:.1f}   TOTAL +{r['dJ']+r['dO']:.1f}\n")
