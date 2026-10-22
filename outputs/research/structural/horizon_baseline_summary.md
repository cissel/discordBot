# SPY horizon and dumb-baseline study (pre-registered)

OOS rows 1959 (2019-01-02 to 2026-10-16), walk-forward yearly refit, expanding window.
Logistic features kept 73 (stale dropped: []). GBM 5d features kept 86 (stale dropped: []).
Chosen C per year (1d, 5d): {2019: (0.03, 0.03), 2020: (0.03, 0.03), 2021: (0.03, 0.03), 2022: (0.03, 0.03), 2023: (0.03, 0.03), 2024: (0.03, 0.03), 2025: (0.03, 0.03), 2026: (0.03, 0.03)}
Cost 0.25 bp per round trip (0.125 bp per unit position change). next_ret_5d = SUM of next 5 daily simple returns.

## Pooled metrics (daily strategies: n = days; 5d: n = non-overlapping weeks per phase)

```
      strategy phase      n hit_rate in_market ann_return sharpe max_dd trades_per_year
ML_1d_logistic   all 1958.0    0.553     0.639      10.9%   0.72 -44.1%              47
        dma200   all 1958.0    0.557     0.798      10.3%   0.85 -23.8%               6
   always_long   all 1958.0    0.551     1.000      15.9%   0.84 -41.2%               0
 dma200_AND_ML   all 1958.0    0.564     0.510       8.4%   0.88 -21.6%              39
   5d_logistic     0  391.0    0.602     0.680       7.1%   0.45 -33.8%               9
   5d_logistic     1  391.0    0.612     0.660       8.0%   0.51 -37.9%               9
   5d_logistic     2  391.0    0.621     0.688       8.9%   0.59 -33.7%              10
   5d_logistic     3  391.0    0.597     0.711       7.7%   0.54 -28.1%               9
   5d_logistic     4  390.0    0.627     0.715       7.3%   0.46 -36.5%              11
   5d_logistic   avg  390.8    0.612     0.691       7.8%   0.51 -34.0%              10
        5d_gbm     0  391.0    0.624     0.578       9.1%   0.59 -33.8%              11
        5d_gbm     1  391.0    0.604     0.568       9.3%   0.66 -26.1%              13
        5d_gbm     2  391.0    0.590     0.581       8.3%   0.55 -41.0%              11
        5d_gbm     3  391.0    0.610     0.545       8.7%   0.64 -34.5%              12
        5d_gbm     4  390.0    0.637     0.579       7.8%   0.51 -36.1%              12
        5d_gbm   avg  390.8    0.613     0.570       8.7%   0.59 -34.3%              12
```

## Per-year Sharpe (5d rows are the phase average)

```
strategy  5d_gbm  5d_logistic  ML_1d_logistic  always_long  dma200  dma200_AND_ML
year                                                                             
2019        2.31         1.99            1.81         2.26    1.44           1.01
2020        0.26        -0.38           -0.90         0.45   -0.07          -0.64
2021        1.13         2.15            1.76         2.11    2.11           1.76
2022       -0.53        -0.76           -0.19        -0.87   -2.11          -2.04
2023        2.33         1.75            3.13         1.78    1.16           2.39
2024        1.03         0.83            2.11         1.79    1.79           2.11
2025        1.14         1.22            0.95         0.87    0.87           1.03
2026        0.37         1.08            0.81         1.19    0.86           0.56
```

## Paired tests

- NW(5) t of daily P&L, ML-1d minus 200dma: 0.13 (mean diff 0.27 bp/day)
- NW(5) t of daily P&L, ML-1d minus always-long: -1.30
- Spearman predicted vs realised (5d_logistic): rho -0.0193, NW(5) t -0.49, n 1954
- Spearman predicted vs realised (5d_gbm): rho 0.0421, NW(5) t 1.02, n 1954
- Spearman predicted vs realised (ML_1d_logistic): rho 0.0245, NW(5) t 1.14, n 1958

## D4 verdict: 200dma dumb baseline vs ML next-day model

Rule: PASS if Sharpe(200dma) >= Sharpe(ML-1d) - 0.1 AND NW t(ML minus 200dma) < 2.0.
Sharpe(200dma) 0.85 vs Sharpe(ML-1d) 0.72 (always-long 0.84, intersection 0.88); NW t 0.13.
**D4 PASS**: the 200dma rule matches or beats the ML model out of sample; the ML-minus-200dma difference is not statistically distinguishable from zero.

## D2 verdict: 5-day horizon vs next-day direction

Rule: PASS if phase-averaged pooled Sharpe(5d) >= Sharpe(ML-1d) + 0.2 AND per-year Sharpe higher in >= 5 of 8 years.
- 5d_logistic: pooled Sharpe 0.51 (delta -0.21 vs ML-1d), higher in 5 of 8 years -> **FAIL**
- 5d_gbm: pooled Sharpe 0.59 (delta -0.13 vs ML-1d), higher in 3 of 8 years -> **FAIL**

**D2 FAIL** overall (neither 5d model clears both conditions).
