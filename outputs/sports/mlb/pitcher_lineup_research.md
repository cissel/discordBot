# MLB Pitcher Quality + Lineup Research (2026-06-16)

## 1. MLB STATS API Endpoint
GET https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=lineups,probablePitcher

### probablePitcher structure (under teams.away/home):
{  "id": 666200, "fullName": "Jesus Luzardo", "link": "/api/v1/people/666200" }

### lineups structure (under game.lineups):
Keys: awayPlayers, homePlayers (each a list of 9 player objects)
BATTING ORDER = LIST INDEX (index 0 = slot 1/leadoff, index 8 = slot 9)
No explicit battingOrder field exists.

Player object fields: id, fullName, firstName, lastName, primaryPosition{code,name,abbreviation}, useName

IMPORTANT: lineups = {} for future/scheduled games. Populated ~60-90min before first pitch.
Yesterday confirmed: gamePk=823452 PHI vs MIA, 9 batters each side.

## 2. probableStarters CSVs
Both files: pitcher_name, team, matchup, home_away, game_date
probableStarters.csv sample: Andrew Abbott | Cincinnati Reds | ARI@CIN | home | 2026-06-14
probableStartersToday.csv sample: Adrian Houser | SF Giants | SF@ATL | away | 2026-06-16
NOTE: No pitcher_id -- need to join via name lookup or statsapi.

## 3. Pybaseball Pitcher Stats (available fields)

pitching_stats_bref(year) -- WORKING:
  ERA, WHIP, SO9(K/9), SO/W(K/BB), BAbip, GB/FB, IP, HR, BB, SO, G, GS, Str%, LD%, PU, mlbID

statcast_pitcher_expected_stats(year) -- WORKING (Savant):
  player_id, pa, bip, ba, est_ba, slg, est_slg, woba, est_woba, era, xera, era_minus_xera_diff

statcast_pitcher_percentile_ranks(year) -- WORKING (Savant):
  player_id, xwoba, xba, xslg, xiso, xobp, brl, brl_percent, exit_velocity, max_ev,
  hard_hit_percent, k_percent, bb_percent, whiff_percent, chase_percent, xera, fb_velocity

statcast_pitcher_exitvelo_barrels(year) -- WORKING (Savant):
  player_id, avg_hit_speed, ev50, brl_percent, brl_pa, ev95percent, anglesweetspotpercent

MLB Stats API /people/{mlbam_id}/stats?stats=season&group=pitching -- WORKING:
  era, whip, strikeOutsPer9Inn, walksPer9Inn, homeRunsPer9, hitsPer9Inn,
  strikeoutWalkRatio, strikeOuts, baseOnBalls, homeRuns, inningsPitched,
  gamesStarted, battersFaced, groundOutsToAirouts, strikePercentage, pitchesPerInning

pitching_stats(year) -- BLOCKED 403 (Fangraphs): ERA, FIP, xFIP, SIERA, WAR not accessible

## 4. Recommended Feature Set for ML
era (BRef/MLB API), xera (Savant), whip (BRef/MLB API),
k_per9/SO9 (BRef), bb_per9 (MLB API), k_pct/bb_pct (Savant percentile),
hard_hit_pct (Savant), whiff_pct (Savant), brl_pct (Savant),
batting_slot 1-9 (lineups list index+1), pitcher_mlbam_id (schedule probablePitcher.id)

## 5. Pipeline
1. Get pitcher MLBAM id from statsapi schedule (probablePitcher.id)
2. Pull ERA/WHIP/K9 from MLB API /people/{id}/stats
3. Pull xERA/wOBA from statcast_pitcher_expected_stats()
4. Pull K%/BB%/HardHit% from statcast_pitcher_percentile_ranks()
5. Get confirmed lineup from statsapi lineups (awayPlayers[i].id, i=batting slot 0-indexed)
6. Join batter features + pitcher quality on game_date + team
