Overnight structural-flow study: 4 pre-registered tests, ~500 hypotheses, 2011-2026 data
```
family                    data span   n       headline                                    verdict
calendar effects          2011-2026   3,886d  0/12 effects stable across halves           FAIL
  (TOM, FOMC, OpEx, CPI, NFP, DOW)      best |t| 1.9; OpEx Fri -6 to -17bp both halves but CI spans 0
DIX deciles -> fwd ret    2012-2026   3,635d  top-bottom 21d +0.65%, NW t 1.0, Spearman .07 FAIL
GEX -> direction          2012-2026   3,635d  long-unless-low-GEX Sharpe .57 vs B&H .71   FAIL
GEX -> realized vol       2012-2026   3,635d  q0 vs q4 next-day |ret| +0.76%, NW t 14.9    PASS (both halves)
GEX -> RV/implied ratio   2012-2026   3,635d  0.89 (q0) vs 0.60 (q4), t 9.1, halves .29/.29 PASS
VIX9D>VIX3M -> RV/implied 2016-2026   2,583d  1.01 inverted vs 0.68 normal, t 6.3          PASS
VIX inversion -> fwd ret  2016-2026   2,583d  +1.3% 21d, t 1.3, CI spans 0                 FAIL
overnight premium (SPY)   2021-2026   1,450d  o/n 3.6bp vs intraday 2.6bp, diff t 0.37     FAIL
  overnight-only strategy                     Sharpe .92 gross vs B&H .95; breakeven cost 0bp
block prints -> drift     2022-2026   250 1/d above +100bp 1mo [+10,+191] n=101;           FAIL
                                              below -33bp [-109,+45]; 8/352 cells clear, 35 expected by chance
```
What survived: nothing directional. GEX and VIX term structure are strong, stable predictors of REALIZED VOL relative to implied, not of direction. Low GEX compresses the variance risk premium (RV/IV 0.60 -> 0.89) but does not collapse it; only term-structure inversion gets mean RV to implied (1.01). That is a sizing / option-selling filter, not a timing signal.

Data bug found: block_outcomes.csv `market_price` is the PRIOR-DAY dividend-adjusted close, not the price at print. 94% of events are "above market" by construction and the 1d outcome includes the print-day move (lookahead). `/spy gaps` and the June block strategies inherited this. The study re-referenced to the same-day close before testing.

Files: outputs/research/structural/ (4 summaries, results CSVs, scripts in python/research/structural/). Nothing committed.