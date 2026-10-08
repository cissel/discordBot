import json

with open('/tmp/room40_rosters.json') as f:
    rosters = json.load(f)
with open('/tmp/ros_weekly_proj.json') as f:
    weekly = json.load(f)
with open('/tmp/all_league_players.json') as f:
    players = json.load(f)

target_teams = {'jhcv','CountDoobie','LeChuckski'}
target_rosters = {r['display_name']: r for r in rosters if r['display_name'] in target_teams}

rows = []
for team, r in target_rosters.items():
    for pid in r['players']:
        pid = str(pid)
        info = players.get(pid, {})
        total = 0.0
        weeks_counted = 0
        for wk, wk_data in weekly.items():
            pts = wk_data.get(pid)
            if pts is not None:
                total += pts
                weeks_counted += 1
        rows.append({
            'team': team,
            'player_id': pid,
            'name': info.get('full_name', f'UNKNOWN_{pid}'),
            'position': info.get('position',''),
            'nfl_team': info.get('team','') or 'FA',
            'injury_status': info.get('injury_status',''),
            'ros_proj_total': round(total,1),
            'weeks_counted': weeks_counted,
            'is_starter': pid in r.get('starters',[]),
        })

rows.sort(key=lambda x: (x['team'], -x['ros_proj_total']))
with open('/tmp/three_team_roster_proj.json','w') as f:
    json.dump(rows, f, indent=2)

for team in target_teams:
    print(f"\n=== {team} ===")
    team_rows = [r for r in rows if r['team']==team]
    for r in team_rows:
        print(f"  {r['position']:3s} {r['name']:25s} {r['nfl_team']:4s} ROS:{r['ros_proj_total']:7.1f}  inj:{r['injury_status']}")
    print(f"  TOTAL ROS proj: {sum(r['ros_proj_total'] for r in team_rows):.1f}")
