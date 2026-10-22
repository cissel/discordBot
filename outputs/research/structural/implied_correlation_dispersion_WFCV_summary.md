# implied_correlation_dispersion: strategy-backtest stage (WFCV)

n=2321 days, 2017-06-23 to 2026-09-17 (limited by vix_term_history.csv start of 2016-06-23). Holdout = final 20% of sample, starting 2024-11-07 (n=465 days), never used to fit anything (the 756d window and 20th-percentile cutoff are fixed constants, not tuned on this data).

BASELINE reproduces vrp_regime_study.py's gate_both (already live in the vol book's GEX/term gate) exactly. PROPOSED adds a walk-forward COR3M bottom-quintile filter (trailing 756-day percentile rank, no lookahead) on top of it. Both charge a conservative 0.05 vol-point cost per gate flip (regime on/off), as a stand-in for the real iron-condor roll cost.

| label                                                       |   eligible_frac |   n_days |   mean_volpts |   hit_rate |   monthly_sharpe |   worst_month_volpts |   nw_t |   mean_H1 |   mean_H2 |   mean_holdout |   n_holdout |
|:------------------------------------------------------------|----------------:|---------:|--------------:|-----------:|-----------------:|---------------------:|-------:|----------:|----------:|---------------:|------------:|
| UNCONDITIONAL (every day, reference only)                   |           1     |     2321 |        1.47   |      0.831 |            0.578 |              -138.71 |   1.12 |     0.437 |     2.502 |          2.476 |         465 |
| BASELINE (live gate: not-GEXq1 AND term-not-inverted)       |           0.768 |     1782 |        1.8296 |      0.857 |            1.818 |              -111.15 |   1.53 |     0.739 |     2.916 |          3.632 |         361 |
| PROPOSED (live gate AND COR3M walk-forward bottom quintile) |           0.315 |      730 |        1.8048 |      0.853 |            0.759 |               -16.87 |   2.9  |     0.309 |     2.417 |          2.501 |         196 |

## Pass checks (PROPOSED vs BASELINE)

- a_sharpe_improves: FAIL
- b_worst_month_not_worse: PASS
- c_both_halves_positive: PASS
- d_nw_t_ge_2: PASS
- e_holdout_positive: PASS

**VERDICT: done_fail (backtest stage)**

At least one criterion fails: adding the COR3M walk-forward gate does not reliably improve on the vol book's existing GEX/term gate once realistic out-of-sample construction and a held-out window are applied. Close this candidate as done_fail at the backtest stage.
