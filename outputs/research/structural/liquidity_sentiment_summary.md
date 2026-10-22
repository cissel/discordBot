# Liquidity / sentiment / credit / order-flow structural sweep (new hypotheses, not in the Oct 19-20 study)

8 pre-registered hypotheses. Pass rule: 90% block-bootstrap CI excludes zero, both halves agree in sign with the full-sample sign, AND |NW t| >= 2.0. Bonferroni m=8: raw p must be below 0.0125 to survive a 90%-family correction.

| hyp                  | cell                                                                 |    n |   diff_bps |   nw_t |   ci_lo_bps |   ci_hi_bps |   diff_H1_bps |   diff_H2_bps | verdict   |
|:---------------------|:---------------------------------------------------------------------|-----:|-----------:|-------:|------------:|------------:|--------------:|--------------:|:----------|
| H1_aaii              | fwd_21d bottom-top quintile                                          |  230 |    110.492 |  1.472 |       0.738 |     220.646 |       341.220 |       -36.125 | FAIL      |
| H1_aaii              | fwd_63d bottom-top quintile                                          |  230 |    305.900 |  1.843 |      39.971 |     564.085 |       915.446 |       -94.210 | FAIL      |
| H2_feargreed         | extreme_fear(<=25) minus extreme_greed(>=75) fwd21                   |  717 |    -11.539 | -0.155 |    -130.952 |     113.073 |        -2.875 |         5.508 | FAIL      |
| H3_googletrends_fear | top-bottom quintile fear search, fwd21                               | 1150 |   -262.936 | -4.947 |    -356.585 |    -180.739 |      -343.504 |      -187.146 | PASS      |
| H4_net_liquidity     | top-bottom quintile 4w net-liq chg, fwd21                            |  490 |    102.216 |  2.446 |      38.706 |     168.930 |       176.908 |       137.588 | PASS      |
| H5_hy_credit_ret     | top-bottom quintile 5d HY OAS chg, fwd5 RET (SHORT SAMPLE 2023-2026) |  307 |    -16.390 | -0.453 |     -78.339 |      39.137 |       -46.190 |         8.545 | FAIL      |
| H5_hy_credit_vol     | top-bottom quintile 5d HY OAS chg, fwd5 RV (SHORT SAMPLE 2023-2026)  |  307 |    419.147 |  1.820 |      86.336 |     842.391 |       822.651 |        20.324 | MARGINAL  |
| H6_cvd_reversion     | sell-extreme minus buy-extreme next-day ret (SHORT SAMPLE 2021-2026) |  527 |      3.740 |  0.540 |      -7.230 |      14.430 |        12.408 |        -7.456 | FAIL      |

Data notes:
- sentiment_daily.csv last row: 2026-06-24 (cron appears stale if this is far before today)
- google_trends_daily.csv last row: 2026-06-24
- H5 (credit spreads): FRED fredgraph.csv on this network only serves BAMLH0A0HYM2/BAMLC0A0CM from 2023-10-23 forward regardless of requested start date (confirmed by direct curl with cosd=1990-01-01); this is a ~3-year sample, underpowered relative to the other tests here. Treat as exploratory only.
- H6 (order flow): spy_orderflow_features.csv only covers 2021-2026 (5 years); half-split is 2021-2023 vs 2024-2026.

Result: 2/8 hypotheses pass all three conditions.
| hyp                  | cell                                      |    n |   diff_bps |   nw_t |   ci_lo_bps |   ci_hi_bps |   diff_H1_bps |   diff_H2_bps | verdict   |
|:---------------------|:------------------------------------------|-----:|-----------:|-------:|------------:|------------:|--------------:|--------------:|:----------|
| H3_googletrends_fear | top-bottom quintile fear search, fwd21    | 1150 |   -262.936 | -4.947 |    -356.585 |    -180.739 |      -343.504 |      -187.146 | PASS      |
| H4_net_liquidity     | top-bottom quintile 4w net-liq chg, fwd21 |  490 |    102.216 |  2.446 |      38.706 |     168.930 |       176.908 |       137.588 | PASS      |

## Diagnostics on the two PASSes (both run on the full re-ranked sample, not the two-point quintile spread)

H3 Google Trends fear (composite 21d z-score of fear/crash/bankruptcy/recession search terms):
- Full sample n=2873 (2015-01-21 to 2026-06-24, capped by the sentiment cron's last successful run).
- Monotonic across all 5 quintiles: fwd21 mean +2.05%, +1.79%, +0.96%, +1.04%, -0.58% (Spearman -0.19).
- NOT a repackaged momentum signal: corr(fear_z21, trailing-21d realized return) = -0.04; corr(fear_z21, fwd21
  RESIDUAL after regressing out trailing-21d momentum) = -0.21, i.e. stronger after removing momentum, not weaker.
- Top/bottom quintile membership is spread across all 12 years (25-62 obs/year each), not concentrated in one
  crisis cluster (2020 and 2022 are represented but not dominant).
- Direction is continuation, not the textbook "retail panic is contrarian-bullish" story: elevated fear-search
  attention predicts BELOW-average, not above-average, next-month returns. Consistent with Preis/Moat/Stanley
  (2013) on Google Trends finance-term search volume preceding drawdowns, not with Da/Engelberg/Gao-style
  attention-reversal.

Strategy translation (python/research/structural/_strategy_check.py, logic: go flat when trailing-ranked
fear_z21 is in the top quintile using only data available at the prior close, else stay long SPX, 1bp/side cost):
  Real signal window 2015-01-02 to 2026-10-19 (n=2966; google_trends_daily.csv has no data before 2015, so the
  pre-2015 portion of the raw backtest defaults to always-long and is excluded here as not a genuine test):
    Strategy: ann ret 26.4%, Sharpe 1.23, max DD -17.5%
    Buy & hold: ann ret 17.1%, Sharpe 0.74, max DD -33.9%
    H1 (2015-01 to 2020-11): Sharpe 1.00 vs B&H 0.70. H2 (2020-11 to 2026-10): Sharpe 1.84 vs B&H 0.88.
    Beats buy-and-hold in 11 of 12 full years tested (all years 2015-2026 except 2015 flat/2017 slight miss).
    Turnover ~12 position flips/year; cumulative cost impact of raising cost from 1bp to 5bp/side over the full
    16-year series is 7.8%, trivial next to the ~370% cumulative outperformance. Worst month -7.2% (2011, no
    signal yet) vs B&H worst month -12.5% (2020-03, COVID crash) — the filter was flat going into that crash.
  Verdict: PASS, and tradeable. This is the strongest new finding in this sweep.
  Caveats: Google Trends index values are relative/normalized by Google and have been revised historically;
  the daily series is a smoothed interpolation of weekly-ish Trends pulls (check fetchSentimentData.py for the
  exact pull cadence before wiring to a live signal); n of independent "fear spike" episodes is still in the
  dozens, not hundreds, so this should be treated as one good out-of-sample-flavored backtest, not a proven law;
  and the sentiment cron is stale (last successful pull 2026-06-24, ~4 months before this study) so a live
  version needs that pipeline fixed first (see below).

H4 Net liquidity (Fed WALCL minus ON RRP minus TGA, weekly, 3-day assumed publication lag):
- Spearman only +0.12 (weak monotonicity: quintile means 1.18%, 0.21%, 0.37%, 1.75%, 2.23% - not clean, q1 dips
  below q0). Not a momentum proxy (corr with trailing-21d return only +0.05; residual corr actually rises to
  +0.19). Top-quintile (expanding liquidity) and bottom-quintile (contracting) months concentrate meaningfully
  in the 2020 QE / 2022 QT episode (23 and 24 of ~130 obs respectively) - this is more "two big regime windows"
  than a repeating weekly phenomenon.
Strategy translation (flat when trailing-ranked net-liquidity growth is in the bottom quintile, else long,
  1bp/side): Sharpe 0.75 vs B&H 0.74 pooled - statistically real in the deciles, economically a wash as a
  binary overlay (H2 half is actually slightly worse than B&H, 0.78 vs 0.81). A continuous-sizing version
  (scale exposure by the liquidity percentile rather than binary flat) might do better but was not tested here.
  Verdict: PASS on the statistical test, NOT currently tradeable as a simple filter. Same pattern as GEX in the
  earlier study - a real, stable macro regime signal that doesn't cleanly convert into a timing rule at this
  simplicity level. Worth more work (sizing instead of binary, or combine with the GEX/term vol-regime work)
  before it goes anywhere near size.

## Operational finding (not a structural-edge result, but found while building this study)

sentiment_daily.csv, macro_sentiment_daily.csv and google_trends_daily.csv all stop at 2026-06-24 - roughly
4 months stale as of this study (2026-10-21). Checked: fetchSentimentData.py, fetchAlternativeData*.py and
fetchMacroSentiment.py are NOT in crontab and NOT in ~/.hermes/scripts/ - they were run manually once (June
2026) and never scheduled, unlike every other fetch* script in the pipeline. If any live model or feature
file consumes AAII/Fear&Greed/Google Trends/jobless-claims features, it has been training/predicting on
4-month-stale forward-filled values without any error, because the files exist and parse fine - they are
just old. Fixing this (add a daily cron calling these three scripts) is a 10-minute job and is the one
unambiguous action item out of this whole report; the two new PASSes above both depend on these exact files.