# Vol book mechanics: exit rules and sizing law (synthetic daily MTM variance swap)

Verification: k=21 MTM equals reference hold-to-expiry (max abs diff 8.5e-14); unconditional mean 2.16 (2011-2016) / 1.70 (2016-2026) vol pts.

Study A PASS rule (live exits beat hold on Sharpe AND worst month in holdout, H1, H2): **FAIL** {'holdout': {'sharpe_5_gt_1': False, 'worst_5_gt_1': True}, 'H1': {'sharpe_5_gt_1': False, 'worst_5_gt_1': True}, 'H2': {'sharpe_5_gt_1': False, 'worst_5_gt_1': True}}

Stop effect (5 minus 4): {'holdout': {'sharpe_5_minus_4': -0.311, 'worst_5_minus_4': 4.49}, 'H1': {'sharpe_5_minus_4': -0.475, 'worst_5_minus_4': 8.68}, 'H2': {'sharpe_5_minus_4': -0.444, 'worst_5_minus_4': 0.28}}

Study B PASS rule (20/VIX over constant: worst month better in all three AND Sharpe within 0.1): **FAIL** {'holdout': {'worst_b_gt_a': False, 'sharpe_b_within': False}, 'H1': {'worst_b_gt_a': False, 'sharpe_b_within': True}, 'H2': {'worst_b_gt_a': True, 'sharpe_b_within': True}}

Secondary (regime lookup beats 20/VIX or RV20 on both Sharpe and worst month): {'holdout': {'c_beats_b': True, 'c_beats_d': False}, 'H1': {'c_beats_b': False, 'c_beats_d': False}, 'H2': {'c_beats_b': True, 'c_beats_d': False}}

## Study A exits

| policy                          | period   |    n |   mean_per_entry |   hit |   monthly_sharpe |   worst_month | worst_month_date   |   nw_t |   frac_PT |   frac_time |   frac_stop |   frac_expiry |   mean_k_exit |
|:--------------------------------|:---------|-----:|-----------------:|------:|-----------------:|--------------:|:-------------------|-------:|----------:|------------:|------------:|--------------:|--------------:|
| 1 hold                          | holdout  | 1047 |            2.386 | 0.84  |            0.802 |        -18.12 | 2015-08            |   3.86 |     0     |       0     |       0     |         1     |          21   |
| 1 hold                          | H1       | 1092 |            1.196 | 0.853 |            0.432 |       -111.17 | 2020-02            |   0.62 |     0     |       0     |       0     |         1     |          21   |
| 1 hold                          | H2       | 1160 |            2.823 | 0.86  |            1.192 |        -16.33 | 2025-03            |   4.86 |     0     |       0     |       0     |         1     |          21   |
| 2 PT                            | holdout  | 1047 |            1.031 | 0.889 |            0.587 |        -15.5  | 2015-08            |   2.72 |     0.803 |       0     |       0     |         0.197 |          10.1 |
| 2 PT                            | H1       | 1092 |           -0.286 | 0.908 |            0.616 |        -91.85 | 2020-02            |  -0.18 |     0.856 |       0     |       0     |         0.144 |           9.9 |
| 2 PT                            | H2       | 1160 |            1.379 | 0.894 |            1.057 |         -7.66 | 2025-03            |   4.53 |     0.817 |       0     |       0     |         0.183 |           9.9 |
| 3 time                          | holdout  | 1047 |            1.151 | 0.771 |            0.621 |        -17.39 | 2015-08            |   2.61 |     0     |       1     |       0     |         0     |          12   |
| 3 time                          | H1       | 1092 |            1.108 | 0.802 |            0.768 |        -31.13 | 2020-02            |   1.63 |     0     |       1     |       0     |         0     |          12   |
| 3 time                          | H2       | 1160 |            1.315 | 0.784 |            0.894 |        -13.13 | 2025-03            |   3.27 |     0     |       1     |       0     |         0     |          12   |
| 4 PT+time                       | holdout  | 1047 |            0.693 | 0.815 |            0.618 |        -14.3  | 2015-08            |   2.11 |     0.672 |       0.328 |       0     |         0     |           7.7 |
| 4 PT+time                       | H1       | 1092 |            0.619 | 0.849 |            0.749 |        -25.44 | 2020-02            |   1.13 |     0.682 |       0.318 |       0     |         0     |           7.9 |
| 4 PT+time                       | H2       | 1160 |            0.909 | 0.831 |            0.964 |         -8.68 | 2025-03            |   3.27 |     0.662 |       0.338 |       0     |         0     |           7.7 |
| 5 PT+time+stop (live)           | holdout  | 1047 |            0.678 | 0.815 |            0.307 |         -9.81 | 2015-08            |   2.41 |     0.672 |       0.294 |       0.033 |         0     |           7.5 |
| 5 PT+time+stop (live)           | H1       | 1092 |            0.645 | 0.848 |            0.274 |        -16.76 | 2018-01            |   1.36 |     0.682 |       0.275 |       0.043 |         0     |           7.6 |
| 5 PT+time+stop (live)           | H2       | 1160 |            0.822 | 0.831 |            0.52  |         -8.4  | 2024-12            |   2.91 |     0.662 |       0.315 |       0.023 |         0     |           7.5 |
| 6 stop                          | holdout  | 1047 |            2.353 | 0.839 |            0.786 |        -12.66 | 2015-08            |   4.23 |     0     |       0     |       0.046 |         0.954 |          20.4 |
| 6 stop                          | H1       | 1092 |            2.445 | 0.853 |            0.567 |        -28.48 | 2018-01            |   2.89 |     0     |       0     |       0.07  |         0.93  |          20.1 |
| 6 stop                          | H2       | 1160 |            2.644 | 0.859 |            0.827 |        -12.96 | 2024-07            |   4.6  |     0     |       0     |       0.04  |         0.96  |          20.5 |
| 7 time+stop (-2x)               | holdout  | 1047 |            1.128 | 0.766 |            0.516 |         -7.7  | 2015-08            |   3.14 |     0     |       0.925 |       0.075 |         0     |          11.5 |
| 7 time+stop (-2x)               | H1       | 1092 |            1.001 | 0.793 |            0.382 |        -14.75 | 2018-01            |   1.85 |     0     |       0.914 |       0.086 |         0     |          11.5 |
| 7 time+stop (-2x)               | H2       | 1160 |            1.176 | 0.784 |            0.536 |         -8.77 | 2024-12            |   3.13 |     0     |       0.94  |       0.06  |         0     |          11.7 |
| 5b PT+time+stop2x (sensitivity) | holdout  | 1047 |            0.665 | 0.809 |            0.306 |         -5.99 | 2015-08            |   2.67 |     0.67  |       0.264 |       0.067 |         0     |           7.3 |
| 5b PT+time+stop2x (sensitivity) | H1       | 1092 |            0.52  | 0.84  |            0.218 |        -12.77 | 2018-01            |   1.24 |     0.679 |       0.252 |       0.069 |         0     |           7.4 |
| 5b PT+time+stop2x (sensitivity) | H2       | 1160 |            0.755 | 0.83  |            0.476 |         -8.4  | 2024-12            |   2.72 |     0.661 |       0.285 |       0.053 |         0     |           7.3 |

## Study B sizing

| arm             | period   |   mean_size |   size_p10 |   size_p90 |    n |   mean_per_entry |   hit |   monthly_sharpe |   worst_month | worst_month_date   |   nw_t |
|:----------------|:---------|------------:|-----------:|-----------:|-----:|-----------------:|------:|-----------------:|--------------:|:-------------------|-------:|
| a const         | holdout  |       1     |      1     |      1     | 1047 |            2.386 | 0.84  |            0.802 |        -18.12 | 2015-08            |   3.86 |
| a const         | H1       |       1     |      1     |      1     | 1092 |            1.196 | 0.853 |            0.432 |       -111.17 | 2020-02            |   0.62 |
| a const         | H2       |       1     |      1     |      1     | 1160 |            2.823 | 0.86  |            1.192 |        -16.33 | 2025-03            |   4.86 |
| b 20/VIX        | holdout  |       1.026 |      0.745 |      1.26  | 1047 |            2.108 | 0.84  |            0.651 |        -21.23 | 2015-08            |   3.25 |
| b 20/VIX        | H1       |       1.075 |      0.615 |      1.497 | 1092 |            0.772 | 0.853 |            0.36  |       -114.43 | 2020-02            |   0.39 |
| b 20/VIX        | H2       |       0.906 |      0.646 |      1.164 | 1160 |            2.453 | 0.86  |            1.171 |        -13.63 | 2025-03            |   4.82 |
| c regime lookup | holdout  |       1.038 |      0.713 |      1.285 | 1047 |            2.202 | 0.84  |            0.687 |        -19.16 | 2015-08            |   3.51 |
| c regime lookup | H1       |       1.037 |      0.629 |      1.285 | 1092 |            0.482 | 0.853 |            0.335 |       -130.07 | 2020-02            |   0.22 |
| c regime lookup | H2       |       0.931 |      0.587 |      1.285 | 1160 |            2.574 | 0.86  |            1.192 |        -11.56 | 2025-03            |   5.09 |
| d 0.11/RV20     | holdout  |       1.008 |      0.631 |      1.355 | 1047 |            2.064 | 0.84  |            0.697 |        -20.59 | 2015-08            |   3.27 |
| d 0.11/RV20     | H1       |       1.069 |      0.543 |      1.355 | 1092 |            1.106 | 0.853 |            0.438 |        -97.13 | 2020-02            |   0.63 |
| d 0.11/RV20     | H2       |       0.928 |      0.544 |      1.355 | 1160 |            2.684 | 0.86  |            1.158 |         -9.3  | 2025-03            |   5.25 |

Caveats: no skew, no gamma path dependence of a real condor mark, credit proxied by the trailing-252 median hold-to-expiry P&L (floor 0.5 vol pts), SPX cash index, study stop is -3x credit (live mark 3x credit is a 2x loss; row 5b is that sensitivity).
