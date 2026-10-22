# SPY overnight vs intraday decomposition, 2021-01 to 2026-10 (1450 days, 1-min bars)

Verdict: FAIL. The overnight premium is NOT reliable in SPY 2021-2026. Overnight beats intraday on raw mean (3.6 vs 2.6 bps/day) but the gap is insignificant (t=0.37), the overnight-only Sharpe (0.92) is already below buy-and-hold (0.95) at zero cost, and it flips negative at 2 bps per side. Break-even cost vs buy-and-hold: 0.0 bps. 38 hypotheses tested; FOMC-overnight (t=2.2, n=46) is the only sub-cell clearing t>2 and does not survive a 38-test correction.

| Leg / condition              |    n | mean bps/d |     t | Sharpe | cum %  |
|------------------------------|-----:|-----------:|------:|-------:|-------:|
| Overnight (all)              | 1450 |       3.61 |  2.21 |   0.92 |  64.2  |
| Intraday (all)               | 1450 |       2.59 |  1.15 |   0.48 |  38.0  |
| Close-to-close (B&H)         | 1450 |       6.18 |  2.28 |   0.95 | 126.6  |
| Overnight minus intraday     | 1450 |       1.03 |  0.37 |        |        |
| Overnight by year 21/22/23/24/25/26 bps | | 6.6 / -5.6 / 2.5 / 9.0 / 4.1 / 5.5 | | | overnight < intraday in 2023 |
| Overnight, prev GEX>0 / <0   | 1288/162 | 3.7 / 2.8 | 2.36 / 0.37 | diff t=0.11 | |
| Overnight, prev VIX hi / lo  | 610/817 | 4.8 / 3.0 | 1.47 / 1.95 | diff t=0.50 | |
| Overnight, event / non-event | 179/1271 | 9.5 / 2.8 | 1.62 / 1.66 | diff t=1.10 | |
| Intraday, event / non-event  | 179/1271 | -6.1 / 3.8 | -0.84 / 1.62 | diff t=-1.30 | |
| Overnight FOMC / CPI / NFP   | 46/69/67 | 15.1 / 11.5 / 3.8 | 2.19 / 0.98 / 0.42 | | |
| First 30 min / last 30 min   | 1450 | 1.27 / -0.71 | 1.64 / -1.08 | 0.69 / -0.45 | |
| Next overnight ~ last30 slope| 1450 | beta -0.079 | -1.22 | R2 0.17% | no predictive power |
| Overnight net 0/0.5/1/2 bps per side Sharpe | 1450 | | | 0.92 / 0.67 / 0.41 / -0.10 | B&H 0.95 |

Method: regular open = first bar at/after 09:30 ET, close = last bar before 16:00 ET (timestamps parsed with their ET offsets; session label 'regular' matched 100% of days). GEX sign and VIX regime (vs trailing 252-day median) lagged one day. Event dates from macro_event_calendars.py. Costs applied as 2 sides per day on the overnight leg.

Files: outputs/research/structural/spy_daily_sessions.csv (reusable daily table), overnight_results.csv, overnight_cum.csv, overnight_intraday.png; code python/research/structural/overnight_intraday.py and overnight_intraday_plot.R.
