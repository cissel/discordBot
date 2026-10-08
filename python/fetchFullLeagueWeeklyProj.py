import requests, json, time

BASE = 'https://api.sleeper.app/v1'
GRAPHQL_URL = 'https://sleeper.com/graphql'

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)

all_pids = set()
for r in rosters:
    all_pids.update(str(p) for p in r['players'])
print('total unique players across whole league:', len(all_pids), flush=True)
all_pids = sorted(all_pids)

def build_query(season, week, season_type, player_ids):
    pid_list = ",".join(f'"{pid}"' for pid in player_ids)
    alias = f"nfl__{season_type}__{season}__{week}__proj"
    query = f"""
    query get_player_score_and_projections_batch {{
      {alias}: stats_for_players_in_week(
        sport: "nfl", season: "{season}", category: "proj", season_type: "{season_type}",
        week: {week}, player_ids: [{pid_list}]
      ) {{
        player_id
        team
        stats
      }}
    }}
    """
    return {"operationName":"get_player_score_and_projections_batch","variables":{},
            "query": " ".join(l.strip() for l in query.splitlines())}

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

season='2026'
season_type='regular'
weekly = {}
for wk in range(3,18):
    wk_totals = {}
    for batch in chunks(all_pids, 150):
        payload = build_query(season, wk, season_type, batch)
        try:
            r = requests.post(GRAPHQL_URL, json=payload, timeout=25)
            data = r.json()
        except Exception as e:
            print(f'week {wk} batch EXCEPTION {e}', flush=True)
            continue
        if data.get('errors'):
            print(f'week {wk} errors', data['errors'][:2], flush=True)
            continue
        key = f"nfl__{season_type}__{season}__{wk}__proj"
        recs = data.get('data',{}).get(key) or []
        for rec in recs:
            wk_totals[rec['player_id']] = (rec.get('stats') or {}).get('pts_ppr')
        time.sleep(0.15)
    weekly[wk] = wk_totals
    print(f'week {wk}: {len(wk_totals)} records', flush=True)
    with open('/tmp/full_league_weekly_proj.json','w') as f:
        json.dump(weekly, f)

print('DONE', flush=True)
