# Calendar and event-structure effects, SPX 2011-2026 and SPY 2016-2026

Pre-registered: 12 effects (TOM, preFOMC, FOMC, OpExFri, OpExWeek, CPI, NFP, Mon-Fri), 3 periods (full, pre-2019, 2019+), 2 series. 72 cells with a p-value, Bonferroni m = 72. Pass rule: bootstrap 90% CI excludes zero in BOTH halves with the same sign.

Result: NOTHING PASSES. Zero of 12 effects clear zero in both halves on either series. Every Bonferroni-adjusted p is 1.00. Best raw t-stat anywhere is 1.91 (SPY OpExFri, full).

| Effect   | SPX pre-2019 diff bps (n) | SPX 2019+ diff bps (n) | SPY pre-2019 (n) | SPY 2019+ (n) | Verdict |
|----------|---------------------------|------------------------|------------------|---------------|---------|
| TOM      | -2.8 (365)                | +2.7 (376)             | -0.3 (141)       | +2.7 (376)    | FAIL, sign flips |
| preFOMC  | -1.5 (61)                 | +15.7 (63)             | +12.2 (24)       | +14.1 (63)    | FAIL, CI spans zero everywhere |
| FOMC     | +9.5 (61)                 | -6.5 (63)              | -18.1 (24)       | -11.2 (63)    | FAIL |
| OpExFri  | -6.3 (92)                 | -14.5 (94)             | -16.6 (36)       | -15.4 (94)    | FAIL, consistent sign but CI includes zero in 3 of 4 halves |
| OpExWeek | +2.5 (453)                | -11.1 (455)            | +0.8 (177)       | -11.2 (457)   | FAIL, 2019+ only |
| CPI      | +1.1 (35)                 | -9.3 (93)              | +0.3 (35)        | -10.9 (93)    | FAIL |
| NFP      | +3.6 (36)                 | +2.6 (91)              | +2.1 (36)        | +1.3 (91)     | FAIL, tiny |
| Mon      | -8.7 (361)                | +4.0 (366)             | +0.1 (139)       | +5.5 (366)    | FAIL, sign flips |
| Tue      | +8.7 (395)                | +0.3 (405)             | +1.9 (154)       | -0.5 (405)    | FAIL, pre-2019 only (CI 0.3 to 16.9) |
| Wed/Thu/Fri | all within +-7         | all within +-7         |                  |               | FAIL |

Equity curves (SPX, long only on event days, 1bp per side, cash otherwise), 3 strongest by full-period |t|:
Buy and hold: Sharpe 0.74 full, 0.62 pre-2019, 0.85 2019+, max DD -33.9%.
Long OpExFri only: Sharpe -0.27 full, -0.20 / -0.32 by half, max DD -20%, total return -12.6% (the effect is negative, so long is wrong-way; it is listed because it is the largest |t|).
Long OpExWeek only: Sharpe 0.10 full, +0.47 pre-2019, -0.16 2019+, max DD -33%.
Long Tue only: Sharpe 0.47 full, 0.71 pre-2019, 0.30 2019+, max DD -11%. Below buy and hold in every period.

Bottom line: on this data none of the textbook calendar effects is tradeable or even stable. The only directionally consistent candidate is a negative OpEx Friday (about -6 to -17 bps per day), but it never clears a 90% bootstrap CI in both halves and its Bonferroni p is 1.0. Treat all 12 as absent for sizing purposes.
