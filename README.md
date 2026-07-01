# Discord Bot

There are many discord bots but this one is mine. I love it dearly.

Built so I could send a message from my phone and have it run scripts, pull data, and post results back to the server - even if I'm not at the helm. Runs on a Raspberry Pi. Speaks Python and R.

---

## commands

### `/weather`
| command | description |
|---|---|
| `today` | current conditions + forecast for Jacksonville, FL |
| `tomorrow` | tomorrow's forecast |
| `jaxradar` | NWS KJAX radar GIF |
| `jaxsat` | satellite GIF of Jacksonville, FL |
| `flradar` | radar GIF for the entire state |
| `usradar` | continental US radar GIF |
| `hurricane` | NOAA 7-day tropical weather outlook |
| `alerts` | active NWS alerts for northeast Florida |

![weather today](outputs/weather/weatherTd.png)
![jax radar](outputs/weather/nwsJaxRadar.gif)
![hurricane](outputs/weather/two7d.png)

### `/surf`
| command | description |
|---|---|
| `surf` | 1-week surf forecast plot for Jax Beach, FL |
| `wavemap` | wave height forecast map GIF |
| `windmap` | wind speed & direction forecast map GIF |
| `buoywaves` | wave height plot from NOAA NDBC buoy #41117 off St. Augustine |
| `tideplot` | tide forecast for Mayport Bar Pilots Dock |
| `windplot` | wind speed & direction plot for Mayport |

![surf forecast](outputs/weather/surf_fcst.png)
![buoy waves](outputs/weather/buoyWaves.png)
![tide plot](outputs/weather/mayportTides.png)
![wind plot](outputs/weather/mayportWinds.png)

### `/ball`
One command. All sports. Returns today's (or tomorrow's) schedule and scores across NHL, MLB, NBA, PGA, NFL, UFC, F1, NASCAR, and World Cup in a single multi-embed response.

### `/nhl`
| command | description |
|---|---|
| `today` | today's NHL games |
| `tomorrow` | tomorrow's NHL games |
| `standings` | NHL standings by conference - seed, record, points, GB |

### `/cats`
Florida Panthers content - next game, scores, 2024 Stanley Cup content, rat celebrations, and more.

### `/nfl`
| command | description |
|---|---|
| `nextgame` | next NFL game on the schedule |
| `standings` | NFL standings by conference - seed, record, PF/PA, streak |
| `wr` | top WR target share chart for the current season |
| `wrstats [season] [player]` | advanced WR stats - separation, YAC over expected, targets gradient (WR only, NGS) |
| `testats [season] [player]` | advanced TE stats - separation, YAC over expected, targets gradient (TE only, NGS) |
| `rbstats [season] [player]` | advanced RB stats - rush yards over expected per carry, NGS efficiency |
| `qbstats [season] [player]` | advanced QB stats - CPOE, aggressiveness %, Pass EPA (NGS) |
| `olstats [season] [player]` | advanced OL blocking stats - pressure rate vs sack rate (participation data) |
| `fantasyscoreboard` | Room 40 fantasy football scoreboard |
| `epamap` | mean EPA choropleth map by NFL team |
| `room40points` | Room 40 fantasy points map |
| `amonra` | I RUN THIS SHIT |
| `fantasywrapped` | Room 40 season full statistical breakdown - standings, draft, positions, trades, luck, GM score |
| `alltimewrapped` | Room 40 all-time record book (2022-present) - cross-season stats, rankings, and GM score |

### `/jags`
| command | description |
|---|---|
| `next` | next Jacksonville Jaguars game |
| `today` | i just wanna party with you |
| `takemyenergy` | ༼ つ ◕◕ ༽つ TAKE MY ENERGY |
| `win` | jags win! |
| `howbout` | how bout them jags |

![epa map](outputs/sports/nfl/epaMap.png)
![target share](outputs/sports/nfl/tgtShr.png)
![room40 map](outputs/sports/nfl/room40map.png)

### `/nba`
| command | description |
|---|---|
| `today` | today's NBA games |
| `tomorrow` | tomorrow's NBA games |
| `standings` | NBA standings by conference - seed, record, win%, GB, streak |

### `/pga`
| command | description |
|---|---|
| `tournament` | live leaderboard for the current PGA Tour event |
| `standings` | season FedEx Cup standings |

### `/mlb`
| command | description |
|---|---|
| `today` | today's MLB schedule with live/final scores |
| `tomorrow` | tomorrow's schedule |
| `standings` | MLB standings by league - seed, record, win%, GB, streak |
| `fantasymap` | World Sillies PF vs PA scatter map - luck vs skill quadrant chart |
| `compare` | sabermetric head-to-head for 2-4 players - stream + roster score, season stats, drop candidate |
| `lineup` | start/sit card for your fantasy roster with scoring signals |
| `pickup` | scans the full FA pool, surfaces top 3 batter + pitcher adds |
| `fantasystandings` | World Sillies league standings |
| `fantasyscoreboard` | current week matchups |
| `fantasyroster` | your roster |
| `fantasyfa` | free agent pool |
| `gmscore` | grades every manager's decisions from last week (A+ to F) |
|| `hotcold` | hottest and coldest batters + pitchers in the last 7 days |
| `ml` | ML fantasy predictions - filter by position (All Positions/All Batters/C/1B/2B/3B/SS/OF/SP/RP) and scope (all/free agents/FA starters) · suspended/IL players excluded · shows injury/roster status icons |
| `playertrends` | recent scoring trend chart for a player |
| `modeldiagnostics` | ML model diagnostics - residuals, loss curves, Spearman history |
| `whohits` | best historical hitters vs today's probable pitchers |
| `mismatch` | batter vs pitcher matchup OPS from 5 years of Statcast data |
| `fantasyrisk` | risk flags across your roster (injury, cold streak, bad matchup) |
| `zonemap` | pitch location subplots by pitch type for any pitcher |
| `fantasyownership` | fantasy points vs ownership % scatter (log scale) - filter by position and FA/all |
| `fantasycumulative` | cumulative fantasy points per player over the season - faceted ggplot, day-by-day |
| `fantasyszn` | cumulative fantasy points for all 8 teams over the season - sorted by standings |

![fantasy risk](outputs/sports/mlb/fantasy/fantasyRisk.png)
![pitch zone map](outputs/sports/mlb/pitchzone_example.png)
![fantasy ownership](outputs/sports/mlb/fantasy/ownership_plot.png)

### `/worldcup`
| command | description |
|---|---|
| `today` | 2026 FIFA World Cup matches today - live scores, kickoff times, venues |
| `tomorrow` | World Cup matches scheduled for tomorrow |
| `standings` | Current group stage standings (all 12 groups A-L, or filter by one) |

### `/ufc`
| command | description |
|---|---|
| `nextevent` | Next upcoming UFC event - date, location, broadcast, main card fights |
| `standings [division]` | UFC rankings by weight class (default: P4P). Division options: `p4p`, `lw`, `lhw`, `hw`, `mw`, `ww`, `fw`, `bw`, `flw`, `strawweight`, `wfly`, `wbw` |

### `/nascar`
| command | description |
|---|---|
| `nextevent` | Next upcoming NASCAR Cup Series race - date, track, location, broadcast |
| `standings` | NASCAR Cup Series driver standings - top 15 with points, wins, top-5 finishes |

### `/f1`
| command | description |
|---|---|
| `nextrace` | next Formula 1 race weekend |
| `standings` | current F1 driver and constructor standings |

### `/markets`
| command | description |
|---|---|
| `chart` | price + volume chart for any stock or ETF ticker (3M/6M/1Y/5Y/10Y) |
| `crypto` | price chart for BTC, ETH, SOL, DOGE - or BTC on-chain metrics (see below) |
| `forecast` | GJR-GARCH / EGARCH / SARIMA animated forecast GIF with Monte Carlo paths - stocks and crypto |
| `macro` | 6-panel macro dashboard - Fed funds rate, 10Y yield, TIPS, CPI, M2, DXY (FRED data, timeframe dropdown) |
| `series` | individual FRED series chart with recession shading - DXY, M2, TIPS, CPI, Fed Funds, 10Y (timeframe dropdown) |
| `sector` | S&P 500 sector treemap - 503 stocks sized by live market cap, colored by daily % change |
| `corr` | 11-asset correlation matrix - SPY, BTC, ETH, gold, VIX, DXY, 10Y, TIPS, CPI, M2, Fed Funds (hierarchically clustered, timeframe dropdown) |
| `vix` | VIX term structure curve (9D / 30D / 3M / 6M / 1Y) + VVIX vol-of-vol gauge with zone bands |
| `commodities` | commodities dashboard - all 12 panels (metals / energy / softs) or filter by category (timeframe dropdown) |
| `fx` | FX dashboard - EUR, GBP, JPY, CHF, CAD, AUD, CNY, MXN vs USD (ECB data, timeframe dropdown) |
| `bonds` | bonds dashboard - TLT, IEF, SHY, HYG, LQD, EMB normalized returns + credit spreads (timeframe dropdown) |
| `news` | latest market headlines from Alpaca news feed - optional ticker filter |
| `fomc` | next 6 FOMC meeting dates with CME-implied Fed Funds rate at each meeting |
| `fedrate` | Fed funds target rate history |
| `yieldcurve` | latest US Treasury yield curve |
| `yieldspread` | 10Y-2Y spread history (timeframe dropdown) |
| `crudeoil` | WTI crude oil price chart |
| `fear` | CNN Fear & Greed Index |
| `movers` | top market gainers and losers |
| `earnings` | upcoming earnings (7 days) or earnings history for a ticker |
| `options` | options flow snapshot for a ticker |
| `short` | most shorted stocks |
| `trades` | recent trade chart |
| `model` | SPY returns model - OLS + NW-HAC robust SEs + AR lags + options flow (1Y/2Y/3Y/5Y/max lookback) |
| `signal` | SPY ML model signal - next-day direction probability and 5-day outlook. Optional `show_context` param shows VIX, yield curve, and event calendar. Flags upcoming FOMC/CPI/NFP events. Use `/spy signal` (under `/spy` group). |

#### SPY ML feature pipeline (daily cron at 4:15 PM ET)
| Script | Output | Description |
|---|---|---|
| `optionsSnapshot.py` | `outputs/research/SPY_options_daily.csv` | ATM IV, IV skew, PCR, term slope |
| `fetchVwapDaily.py` | `outputs/markets/cache/spy_vwap_daily.csv` | Session VWAP deviation, cross count, vol concentration (9 features). Backfill: `--backfill YYYY-MM-DD YYYY-MM-DD` |
| `fetchBlockSignals.py` | `outputs/research/block_events.csv`, `block_outcomes.csv` | Large SPY block trades (>$300M, >0.5% deviation, tiered 0.8% high-dev flag). Maintains forward outcomes at 1d/3d/1w/2w/1mo horizons. Backfill: `--backfill YYYY-MM-DD YYYY-MM-DD` |
| `fetchIntradayBars.py` | `outputs/markets/intraday/SPY_{YEAR}_1min.csv` | Full extended-hours 1-min bars (4am-8pm ET), year-partitioned. Backfill: `--backfill YYYY-MM-DD YYYY-MM-DD` |
| `buildIntradayFeatures.py` | `outputs/features/markets/spy_intraday_features.csv` | Aggregates 1-min bars to 12 daily features: first/last-hour return, AM/PM range, gap fill, open drive, vol front-loading, premarket ret/vol, overnight gap |
| `fetchOrderFlowDaily.py` | `outputs/markets/orderflow/SPY_{YEAR}_cvd.csv` | SPY tick-level CVD via Lee-Ready (SIP feed), year-partitioned. Backfill: `--backfill YYYY-MM-DD YYYY-MM-DD` |
| `buildOrderFlowFeatures.py` | `outputs/features/markets/spy_orderflow_features.csv` | Aggregates minute CVD to 14 daily order-flow features: CVD normalized by traded vol, intensity ratio (sell/buy), large trade ratio, momentum, peak hour |
| `fetchGexDaily.py` | `outputs/markets/cache/spy_gex_daily.csv` | SqueezeMetrics DIX/GEX daily CSV (free). GEX 2011-present, 100% training coverage. 9 features: gex_b, gex_sign, gex_z21, gex_z63, gex_chg_1d/5d, dix, dix_z21, dix_chg_5d |
| `fetchVixTermHistory.py` | `outputs/markets/cache/vix_term_history.csv` | VVIX + VIX9D/3M/6M daily history via Yahoo Finance (free). 2,512 rows 2016-present, ~95% coverage. 6 features: vvix, vvix_z21, vvix_chg_5d, vix_term_slope (VIX3M-VIX9D normalized), vix_term_z21, vix_rv_spread |
| `buildRegimeFeatures.py` | `outputs/features/markets/regime_feature_importance.csv` | Per-regime Spearman rho table: each feature vs next_dir_1d and next_ret_5d, split by bull/bear/chop. Includes regime_divergence column (how much rho varies across regimes). Run after buildSpyFeatures.py to identify regime-specific feature sets. |
| `buildRegimePredModel.py` | `models/markets/spy/spy_regime_pred_{date}.pkl` | 3-class GBM/Logistic predicting TOMORROW's market regime (bull/bear/chop). 81.4% val accuracy vs 32.9% naive baseline. Used by predictSpy.py to route direction prediction to the appropriate regime-specific model. |
| `buildOvernightModel.py` | `models/markets/overnight/overnight_dir_{DATE}.pkl` | Logistic model predicting next-day overnight gap direction. Logs to `models/meta/overnight_experiment_log.csv` |
| `evalSpyModel.py` | `outputs/features/markets/eval_spy_*.csv` | Re-runs val-set inference on all 5 SPY models, writes prediction CSVs for diagnostics |

**Discord commands:**
- `/spy signal` - next-day direction + 5-day outlook (under `/spy` group)
- `/spy diagnostics [regenerate]` - 5-panel diagnostic plot: calibration, residuals, rolling accuracy, run history
- `/spy gaps` - latest SPY block order gaps: block price, exchange, direction, deviation %, dollar value, and % move from market price at each horizon (1d/3d/1w/2w/1mo)
- `/btcsignal` - BTC ML model signal: next-day direction + 5-day outlook + on-chain context (MVRV, NUPL, hashrate, halving cycle, dominance)
- `/btcdiagnostics [regenerate]` - BTC 5-panel diagnostic plot: predicted vs actual, residuals, rolling 30d accuracy, run history, MVRV zone accuracy
- `/gex [timeframe]` - Dealer GEX + DIX chart: gamma exposure regime (suppression vs destabilizing) + dark pool buying pressure, 5 timeframes (3M/6M/1Y/3Y/ALL). Data: SqueezeMetrics 2011-present. Refreshes data on each call. Lives under `/markets gex`.
- `/snapshot [timeframe]` - markets overview: Row 1 equities (SPY/QQQ/DIA/IWM), Row 2 bonds (SHY/IEF/TLT/HYG), Row 3 forex+DXY (EUR/USD, GBP/USD, USD/JPY, UUP). Timeframes: intraday | 1w | 1mo | 3mo | 6mo | 1y. Standalone command (markets group at 25-cmd Discord limit).

![stock chart](outputs/markets/stockchart.png)
![crypto chart](outputs/markets/cryptochart.png)
![btc s2f](outputs/markets/btcS2F.png)
![forecast](outputs/markets/forecast_SPY_1d_1mo_mcpaths.gif)
![yield curve](outputs/markets/yield_curve.png)

#### `/markets crypto` - BTC on-chain metrics
| coin/metric | description |
|---|---|
| `BTC / ETH / SOL / DOGE` | price chart - last 24h (1-min bars), 1W, 1M, 3M, 6M, 1Y, 2Y, 5Y, 10Y, max |
| `BTC Hashrate` | network hashrate (EH/s) over time |
| `BTC Rainbow Chart` | power law regression + halving cycle bands |
| `BTC NUPL` | Net Unrealized Profit/Loss by sentiment zone |
| `BTC MVRV Ratio` | Market Value to Realized Value |
| `BTC S2F Power Law` | Stock-to-Flow log-linear model fit on historical data with forecast through next 2 halvings (~2028, ~2032) |
| `BTC Dominance` | BTC % of total crypto market cap |
| `BTC Realized Price` | on-chain cost basis layers - realized price, true market mean, active investor mean, STH realized price |
| `BTC Miner Capitulation` | price relative to last difficulty bottom, colored by blocks elapsed since capitulation event |

### `/spy`
SPY ML model commands. Block gap detection runs daily via cron and accumulates forward outcomes over 1d/3d/1w/2w/1mo horizons. Paper trading via Alpaca paper account (requires `APCA_PAPER_API_KEY_ID` + `APCA_PAPER_API_SECRET_KEY` in `.env`).

| command | description |
|---|---|
| `signal` | next-day direction + 5-day outlook. Optional `show_context` for VIX/yield curve/event calendar. Flags FOMC/CPI/NFP windows |
| `diagnostics [regenerate]` | 5-panel diagnostic plot: calibration curve, residuals, rolling 30d accuracy, run history |
| `gaps` | latest SPY block order gaps - block price, exchange, direction (above/below market at print), deviation %, dollar value, and % move from market price at each forward horizon |
| `trade [execute]` | paper trade execution - shows signal, proposed order (Kelly-sized), account equity, risk checks. Default: dry run preview. Pass `execute:True` to actually place the market order |
| `status` | paper account status - equity, current SPY position, recent orders, trade log summary |

### `/space`
| command | description |
|---|---|
| `nextlaunch` | next launch from Kennedy Space Center, Cape Canaveral, FL |
| `isspass` | upcoming ISS passes visible from Jacksonville, FL |

### `/jaxcams`
Live traffic camera grid from FL511 - searchable by location name (Dames Point, Fuller Warren, etc).

### `/jaxplanes`
Plot of active aircraft within 150nm of KJAX.

![jax planes](outputs/aerospace/jaxPlanes.png)

### `/jaxships`
Maritime traffic map for the St. Johns River and Jacksonville port.

![jax ships](outputs/maritime/jaxShips.png)

### `/jax realestate`
Duval County residential real estate - median price per sqft by zip code, and price trends over time.

![realestate sqft](outputs/jax/realestate_sqft.png)
![realestate time](outputs/jax/realestate_time.png)

### `/osrs`
| command | description |
|---|---|
| `hiscores` | crew hiscores leaderboard - total level or any skill |
| `lvl` | full skill sheet for a player |

![osrs](outputs/osrs/osrs.png)

### `/dj`
Plays music from a Pioneer rekordbox USB library in a voice channel. Supports queue, skip, stop, and playlist browsing by genre or artist.

### `/billboard`
Top music charts sourced from Apple Music / iTunes. Updated daily.

| mode | description |
|---|---|
| `songs` | Top 10 songs right now (default) |
| `artists` | Top 10 artists right now, ranked by number of songs in the top charts |
| `genre` | Top 10 songs in a chosen genre (Pop, Hip-Hop/Rap, Rock, Country, R&B/Soul, Electronic, Dance, Latin, K-Pop, J-Pop, Reggae, Christian/Gospel, Classical, Jazz, Soundtrack) |

### misc
`/ping`, `/duval`, `/westside`, `/ts`, `/goodmorning`, `/dontavius`, `/r2`, `/chucktronic`, `/serversdown`, `/usa`, and a handful of others best discovered in the wild.

---

### `/history`
| command | description |
|---|---|
| `server` | total server message history - cumulative messages by channel over time |
| `channel` | per-channel message history |
| `user` | cumulative message history by user |
| `daily` | daily message volume plot |
| `userreport` | channel activity breakdown for a specific user - same stacked area as server but filtered to one person |
| `invitegraph` | bubble network of who invited who to the server |
| `repograph` | repo growth over time - cumulative lines of code + weekly commits |
| `repofilesize` | per-file LOC growth over time - stacked area chart, top 20 files (blue=python, green=R) |
| `repotreemap` | current repo file sizes as a treemap - box area = LOC, grouped by directory |

![server history](outputs/metrics/allMessages.png)
![channel history](outputs/metrics/channelMessages.png)
![user history](outputs/metrics/userMessages.png)
![daily messages](outputs/metrics/dailyMessages.png)
![invite graph](outputs/server/invite_graph.png)
![repo graph](outputs/server/repo_graph.png)
