import requests, json, time
from collections import defaultdict

LEAGUE_ID = '1259616442014244864'
BASE = 'https://api.sleeper.app/v1'

users = requests.get(f'{BASE}/league/{LEAGUE_ID}/users', timeout=20).json()
user_map = {u['user_id']: {'display_name': u.get('display_name',''), 'team_name': u.get('metadata',{}).get('team_name','') or u.get('display_name','')} for u in users}

rosters_raw = requests.get(f'{BASE}/league/{LEAGUE_ID}/rosters', timeout=20).json()
roster_map = {}
for r in rosters_raw:
    u = user_map.get(r['owner_id'], {})
    roster_map[str(r['roster_id'])] = {
        'owner_id': r['owner_id'],
        'display_name': u.get('display_name',''),
        'team_name': u.get('team_name',''),
        'wins': r['settings'].get('wins',0),
        'losses': r['settings'].get('losses',0),
        'fpts': r['settings'].get('fpts',0) + r['settings'].get('fpts_decimal',0)/100,
        'fpts_against': r['settings'].get('fpts_against',0) + r['settings'].get('fpts_against_decimal',0)/100,
    }

league_info = requests.get(f'{BASE}/league/{LEAGUE_ID}', timeout=20).json()
draft_id = league_info.get('draft_id')
print(f'Draft ID: {draft_id}')

draft_picks_raw = requests.get(f'{BASE}/draft/{draft_id}/picks', timeout=30).json()
draft_picks = []
for p in draft_picks_raw:
    meta = p.get('metadata', {})
    draft_picks.append({
        'pick_no': p['pick_no'],
        'round': p['round'],
        'draft_slot': p['draft_slot'],
        'player_id': p['player_id'],
        'roster_id': str(p['roster_id']),
        'team_name': roster_map.get(str(p['roster_id']),{}).get('team_name',''),
        'display_name': roster_map.get(str(p['roster_id']),{}).get('display_name',''),
        'player_name': f"{meta.get('first_name','')} {meta.get('last_name','')}".strip(),
        'position': meta.get('position',''),
        'nfl_team': meta.get('team',''),
    })
print(f'Draft picks: {len(draft_picks)}')

matchups = {}
for wk in range(1, 18):
    wk_data = requests.get(f'{BASE}/league/{LEAGUE_ID}/matchups/{wk}', timeout=20).json()
    if wk_data:
        matchups[str(wk)] = [{'roster_id': str(m['roster_id']), 'matchup_id': m['matchup_id'], 'points': m.get('points',0), 'players_points': m.get('players_points',{}), 'starters': m.get('starters',[]), 'players': m.get('players',[])} for m in wk_data]
    time.sleep(0.1)
print(f'Matchup weeks fetched: {len(matchups)}')

def invert_multi(raw_dict):
    """Convert Sleeper {player_id: roster_id} -> {roster_id: [player_id, ...]}"""
    result = defaultdict(list)
    for pid, rid in (raw_dict or {}).items():
        result[str(rid)].append(str(pid))
    return dict(result)

all_trades   = []
all_waivers  = []

for wk in range(1, 18):
    txns = requests.get(f'{BASE}/league/{LEAGUE_ID}/transactions/{wk}', timeout=20).json()
    for t in txns:
        if t.get('status') != 'complete':
            continue
        txn_type = t.get('type')
        adds_multi  = invert_multi(t.get('adds')  or {})
        drops_multi = invert_multi(t.get('drops') or {})
        base = {
            'week':           wk,
            'transaction_id': t.get('transaction_id'),
            'roster_ids':     [str(r) for r in t.get('roster_ids', [])],
            'adds':           adds_multi,
            'drops':          drops_multi,
            'draft_picks':    t.get('draft_picks', []),
        }
        if txn_type == 'trade':
            all_trades.append({**base, 'creator': str(t.get('creator',''))})
        elif txn_type in ('waiver', 'free_agent'):
            all_waivers.append(base)
    time.sleep(0.1)
print(f'Trades found: {len(all_trades)}')
print(f'Waivers/FA found: {len(all_waivers)}')

all_player_ids = set(p['player_id'] for p in draft_picks)
for wk, wk_data in matchups.items():
    for m in wk_data:
        all_player_ids.update(m.get('players_points', {}).keys())
for t in all_trades + all_waivers:
    for pids in list(t['adds'].values()) + list(t['drops'].values()):
        all_player_ids.update(pids)
all_player_ids.discard('BYE')
all_player_ids = list(all_player_ids)
print(f'Unique player IDs: {len(all_player_ids)}')

print('Fetching players DB (~17MB)...')
players_raw = requests.get(f'{BASE}/players/nfl', timeout=120).json()
players = {}
for pid in all_player_ids:
    p = players_raw.get(str(pid), {})
    if p:
        players[str(pid)] = {
            'full_name': p.get('full_name') or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            'position': p.get('position',''),
            'team': p.get('team','FA'),
            'search_rank': p.get('search_rank', 9999),
        }
print(f'Players fetched: {len(players)}')

# Build player_weekly_pts from matchup players_points (the only reliable source)
# Then supplement dropped FA players using ESPN's public fantasy stats API
print('Building player_weekly_pts from matchup data...')
player_weekly_pts = {}  # {player_id: {week_str: pts}}
for wk, wk_data in matchups.items():
    for m in wk_data:
        for pid, pts in m.get('players_points', {}).items():
            if pid == 'BYE':
                continue
            if pid not in player_weekly_pts:
                player_weekly_pts[pid] = {}
            player_weekly_pts[pid][wk] = pts

# Identify dropped players with gaps in coverage
dropped_players = {}  # {player_id: earliest_drop_week}
for t in all_waivers:
    wk = int(t['week'])
    for rid, pids in t.get('drops', {}).items():
        for pid in (pids if isinstance(pids, list) else [pids]):
            pid = str(pid)
            if pid and pid != 'BYE':
                if pid not in dropped_players or dropped_players[pid] > wk:
                    dropped_players[pid] = wk
# Also check trade drops
for t in all_trades:
    wk = int(t['week'])
    for rid, pids in t.get('drops', {}).items():
        for pid in (pids if isinstance(pids, list) else [pids]):
            pid = str(pid)
            if pid and pid != 'BYE':
                if pid not in dropped_players or dropped_players[pid] > wk:
                    dropped_players[pid] = wk

# Find which players/weeks are missing
missing_player_weeks = {}  # {player_id: [wk_int, ...]}
for pid, drop_wk in dropped_players.items():
    missing_wks = []
    for wk in range(drop_wk, 18):
        if pid not in player_weekly_pts or str(wk) not in player_weekly_pts[pid]:
            missing_wks.append(wk)
    if missing_wks:
        missing_player_weeks[pid] = missing_wks

print(f'Dropped players with missing week data: {len(missing_player_weeks)}')

# Fetch missing points from ESPN fantasy stats API
# ESPN endpoint: https://fantasy.espn.com/apis/v3/games/ffl/seasons/2025/segments/0/leagues/0?scoringPeriodId={wk}&view=kona_player_info
# Simpler: use ESPN's player stats endpoint per week
# We'll use the nfl.com fantasy stats or ESPN's public player endpoint
# ESPN: GET https://site.api.espn.com/apis/fantasy/v2/games/ffl/seasons/2025/segments/0/leagues/0?scoringPeriodId={wk}&view=kona_player_info
# Actually simplest: ESPN public stats endpoint returns pts by week per player

ESPN_SCORING_MAP = {
    # passing
    'passingYards': 0.04, 'passingTouchdowns': 4, 'interceptions': -2,
    # rushing
    'rushingYards': 0.1, 'rushingTouchdowns': 6,
    # receiving (PPR: 1pt per rec)
    'receivingYards': 0.1, 'receivingTouchdowns': 6, 'receivingReceptions': 1,
    # misc
    'fumbles': -2, 'fumblesLost': -2,
    # kicking (approximate - will be imprecise without exact league settings)
    'madeFieldGoalsFrom50Plus': 5, 'madeFieldGoalsFrom40To49': 4,
    'madeFieldGoalsFrom30To39': 3, 'madeFieldGoalsFromUnder30': 3,
    'missedFieldGoals': -1, 'extraPointsMade': 1, 'blockedKickForTouchdowns': 2,
}

def fetch_espn_pts_for_week(wk):
    """Fetch all player fantasy points for a given week from ESPN public API."""
    url = (f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2025'
           f'/segments/0/leagues/336358?scoringPeriodId={wk}&view=kona_player_info')
    headers = {'x-fantasy-filter': '{"players":{"filterSlotIds":{"value":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,23,24]},"limit":1000,"offset":0,"sortPercOwned":{"sortAsc":false,"sortPriority":1},"filterStatsForCurrentSeasonScoringPeriodId":{"value":[' + str(wk) + ']}}}'}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        pts_map = {}
        for p in data.get('players', []):
            espn_id = str(p.get('id', ''))
            # Get scoring period stats
            for stat_entry in p.get('playerPoolEntry', {}).get('playerRatings', {}).get(str(wk), {}).get('statSourceId', {}).get('0', {}).get('stats', {}).items():
                pass
        return pts_map
    except Exception:
        return {}

# Simpler approach: use ESPN's player stats endpoint that returns actual pts
# https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{espn_id}/stats
# But we don't have ESPN IDs mapped to Sleeper IDs easily.

# Best practical approach for this league: use nflfastR weekly data via GitHub
# OR: just use the Sleeper API's /projections endpoint which has actual pts
# /projections/nfl/2025/{week} returns player projections, but actual stats
# are in /stats/nfl/2025/{week} which we already know is broken.

# ACTUAL FIX: The Sleeper /stats endpoint requires the correct season_type param.
# Let's try 'regular' without the year prefix:
print('Fetching actual player stats from Sleeper stats endpoint (corrected)...')
weekly_stats_raw = {}
for wk in range(1, 18):
    # Try the correct endpoint format
    resp = requests.get(f'{BASE}/stats/nfl/regular/2025/{wk}', timeout=30)
    if resp.status_code == 200:
        wk_stats = resp.json()
        if wk_stats:
            # Check if first entry has real stats (not just rank metadata)
            sample = next(iter(wk_stats.values()), {}) if wk_stats else {}
            real_keys = [k for k in (sample or {}) if k not in ('pos_rank_half_ppr','pos_rank_ppr','pos_rank_std','rank_half_ppr','rank_ppr','rank_std')]
            if real_keys:
                weekly_stats_raw[str(wk)] = wk_stats
                print(f'  Week {wk}: {len(wk_stats)} players, sample keys: {real_keys[:4]}')
            else:
                print(f'  Week {wk}: rank-only data (useless)')
                weekly_stats_raw[str(wk)] = {}
        else:
            weekly_stats_raw[str(wk)] = {}
    else:
        print(f'  Week {wk}: HTTP {resp.status_code}')
        weekly_stats_raw[str(wk)] = {}
    time.sleep(0.15)

# If we got real stats, use them to fill gaps
scoring = league_info.get('scoring_settings', {})
def stats_to_fpts(stats_dict):
    if not stats_dict:
        return 0.0
    total = 0.0
    for stat, val in stats_dict.items():
        sc = scoring.get(stat, 0) or 0
        total += float(val or 0) * float(sc)
    return total

gaps_filled = 0
for pid, missing_wks in missing_player_weeks.items():
    for wk in missing_wks:
        wk_str = str(wk)
        raw_stats = weekly_stats_raw.get(wk_str, {}).get(pid)
        if raw_stats:
            pts = stats_to_fpts(raw_stats)
            if pts > 0:
                if pid not in player_weekly_pts:
                    player_weekly_pts[pid] = {}
                player_weekly_pts[pid][wk_str] = pts
                gaps_filled += 1

print(f'Gaps filled from stats endpoint: {gaps_filled}')
print(f'Remaining gaps: {sum(len(v) for v in missing_player_weeks.values()) - gaps_filled}')

# Fetch FantasyPros half-PPR ADP (pre-draft consensus, matches league scoring)
print('Fetching FantasyPros ADP...')
adp_map = {}
try:
    import re
    from bs4 import BeautifulSoup
    adp_resp = requests.get(
        'https://www.fantasypros.com/nfl/adp/half-point-ppr-overall.php',
        headers={'User-Agent': 'Mozilla/5.0'}, timeout=15
    )
    if adp_resp.status_code == 200:
        soup = BeautifulSoup(adp_resp.text, 'html.parser')
        table = soup.find('table', {'id': 'data'})
        if table:
            def _clean_adp_name(n):
                n = re.sub(r'[A-Z]{2,4}\(\d+\)$', '', n).strip()
                n = re.sub(r'\s+(Jr\.?|Sr\.?|II|III|IV)$', '', n, flags=re.IGNORECASE).strip()
                return n.lower()
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 4:
                    try:
                        adp_map[_clean_adp_name(cols[1].get_text(strip=True))] = float(cols[3].get_text(strip=True))
                    except:
                        pass
    print(f'ADP entries fetched: {len(adp_map)}')
except Exception as e:
    print(f'ADP fetch failed (non-fatal): {e}')

output = {
    'roster_map':       roster_map,
    'draft_picks':      draft_picks,
    'matchups':         matchups,
    'trades':           all_trades,
    'waivers':          all_waivers,
    'players':          players,
    'roster_positions': league_info.get('roster_positions', []),
    'scoring_settings': league_info.get('scoring_settings', {}),
    'player_weekly_pts': player_weekly_pts,
    'adp_map':          adp_map,
}
with open('/tmp/sleeper_data.json', 'w') as f:
    json.dump(output, f)
print('DONE - saved to /tmp/sleeper_data.json')
