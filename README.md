# Discord Bot

There are many discord bots but this one is mine. I love it dearly.

Built so I could send a message from my phone and have it run scripts, pull data, and post results back to the server - even if I'm not at the helm. Runs on a Raspberry Pi. Speaks Python and R.

**191+ commits · 15,500+ lines · March 2025 - present**

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
| `hotcold` | hottest and coldest batters + pitchers in the last 7 days |
| `playertrends` | recent scoring trend chart for a player |
| `whohits` | best historical hitters vs today's probable pitchers |
| `mismatch` | batter vs pitcher matchup OPS from 5 years of Statcast data |
| `fantasyrisk` | risk flags across your roster (injury, cold streak, bad matchup) |
| `zonemap` | pitch location subplots by pitch type for any pitcher |
| `fantasyownership` | fantasy points vs ownership % scatter (log scale) - filter by position and FA/all |

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
| `regress` | multivariate OLS regression with NW-HAC robust SEs, ADL lags, VIF - 25-variable dropdowns for target and regressors |
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

![server history](outputs/metrics/allMessages.png)
![channel history](outputs/metrics/channelMessages.png)
![user history](outputs/metrics/userMessages.png)
![daily messages](outputs/metrics/dailyMessages.png)
![invite graph](outputs/server/invite_graph.png)
![repo graph](outputs/server/repo_graph.png)

### misc
`/ping`, `/duval`, `/westside`, `/ts`, `/goodmorning`, `/dontavius`, `/r2`, `/chucktronic`, `/serversdown`, and a handful of others best discovered in the wild.
