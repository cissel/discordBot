import requests, json, time

LEAGUE_ID = '1259616442014244864'
BASE = 'https://api.sleeper.app/v1'
GRAPHQL_URL = 'https://sleeper.com/graphql'

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)

target_teams = {'jhcv','CountDoobie','LeChuckski'}
target_rosters = [r for r in rosters if r['display_name'] in target_teams]
all_pids = set()
for r in target_rosters:
    all_pids.update(str(p) for p in r['players'])
print('total unique players across 3 teams:', len(all_pids), flush=True)
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

season='2026'
season_type='regular'
weekly = {}
for wk in range(3,18):
    payload = build_query(season, wk, season_type, all_pids)
    try:
        r = requests.post(GRAPHQL_URL, json=payload, timeout=20)
        data = r.json()
    except Exception as e:
        print(f'week {wk} EXCEPTION {e}', flush=True)
        continue
    if data.get('errors'):
        print(f'week {wk} errors', data['errors'][:2], flush=True)
        continue
    key = f"nfl__{season_type}__{season}__{wk}__proj"
    recs = data.get('data',{}).get(key) or []
    weekly[wk] = {rec['player_id']: (rec.get('stats') or {}).get('pts_ppr') for rec in recs}
    print(f'week {wk}: {len(recs)} records', flush=True)
    with open('/tmp/ros_weekly_proj.json','w') as f:
        json.dump(weekly, f)
    time.sleep(0.2)

print('DONE', flush=True)
