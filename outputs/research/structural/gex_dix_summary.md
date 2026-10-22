# GEX / DIX dealer positioning study, SPX 2012-04 to 2026-10 (3635 ranked days)

Signals ranked on trailing 252 days only; forward returns use closes after t. NW t-stats (lag = horizon-1), 10,000-draw block bootstrap 90% CIs, both halves (2011-2018 vs 2019-2026; D: 2016-2020 vs 2021-2026). 18 primary hypotheses (54 with halves). 1bp per side.

| Test | Spread (full) | NW t | 90% CI | n hi / lo | Halves | Verdict |
|---|---|---|---|---|---|---|
| A DIX top-bottom decile, fwd 5d | +0.05% | 0.19 | [-0.36, +0.45]% | 453 / 422 | sign flips | FAIL |
| A DIX top-bottom decile, fwd 21d | +0.65% | 1.00 | [-0.43, +1.67]% | 447 / 422 | same sign, H2 half the size | FAIL (Spearman 0.07, not monotonic: deciles 4-5 lowest) |
| B GEX q0-q4, next-day abs ret | +0.76% | 14.9 | [+0.68, +0.84]% | 689 / 988 | agree | PASS |
| B GEX q0-q4, realized 5d vol (ann) | +12.9 pts | 9.6 | [+10.7, +15.2] | 689 / 986 | agree | PASS |
| B GEX q0-q4, RV5 / VIX ratio | +0.29 (0.89 vs 0.60) | 9.1 | [+0.24, +0.34] | 689 / 986 | agree (0.29 / 0.29) | PASS |
| B GEX neg-pos, RV5 / VIX ratio | +0.23 (0.93 vs 0.70) | 5.5 | [+0.17, +0.30] | 317 / 3314 | agree | PASS |
| B GEX q0-q4, realized minus implied variance | +0.023 | 1.97 | [+0.007, +0.045] | 689 / 986 | sign only, H1 tiny | WEAK |
| C lowGEX+highDIX minus highGEX+lowDIX, fwd 5d | +0.26% | 0.71 | [-0.37, +0.85]% | 212 / 270 | agree in sign | FAIL |
| C same, fwd 21d | +1.57% | 2.43 | [+0.50, +2.62]% | 212 / 270 | agree, H2 t = 1.1 | MARGINAL |
| D vix9d>vix3m inverted-normal, fwd 5d / 21d | +0.23% / +1.31% | 0.57 / 1.29 | both span 0 | 320 / 2263 | agree in sign | FAIL |
| D inverted-normal, RV5 / VIX ratio | +0.33 (1.01 vs 0.68) | 6.3 | [+0.25, +0.42] | 320 / 2263 | agree | PASS |

Key answer on the variance risk premium: low or negative GEX raises realized vol sharply and compresses the VRP, but on average realized still stays below implied (ratio 0.89 in q0, 0.93 when GEX < 0). Only 26-27% of low-GEX days see RV5 > VIX versus 8% in q4. The VRP shrinks; it does not collapse. The excess-variance spread is positive but weak (t about 2, H1 near zero). Term structure inversion is the one bucket where mean RV reaches implied (ratio 1.01).

Strategy (single strongest finding, GEX q0-q4 abs return, t = 14.9): long SPX unless GEX is in the bottom trailing quintile, else flat. Full: ann ret 6.6%, vol 11.7%, Sharpe 0.57, max DD -26.0%, 81% time in market, 12.7 round trips/yr. Buy and hold same window: 11.8%, 16.7%, Sharpe 0.71, max DD -33.9%. Halves: filter Sharpe 0.39 / 0.69 vs B&H 0.68 / 0.75. The filter cuts vol and drawdown but loses Sharpe in both halves because low-GEX days carry positive average returns. FAIL as a timing strategy; with 18 hypotheses the return findings (A, C, D returns) do not survive any haircut.

Decision: GEX is a strong, stable realized-vol regime predictor (useful for sizing or option selling, not direction). DIX has no usable directional edge at 5d or 21d. Do not trade the long/flat filter.
