SPY + gold trend study (pre-registered holdout test of the T3 gold observation)

Verdict: FAIL

Rule: PASS if on the HOLDOUT arm 3 (SPY+gold trend, N=2 park) beats arm 1 (SPY trend x inv vol) on Sharpe by >= 0.20 AND has a smaller max DD AND the block-bootstrap 95% CI lower bound on the holdout Sharpe difference is above -0.05, AND arm 3 Sharpe is higher on the CONFIRM window. PARTIAL if the holdout Sharpe and DD conditions hold but confirm fails or the CI straddles. FAIL otherwise.

HOLDOUT: 2012-04-27 to 2016-12-28, n = 1176 days, H1/H2 split at 2014-08-29.
CONFIRM: 2016-12-29 to 2026-10-19, n = 2464 days, H1/H2 split at 2021-07-01.
Cost 0.25bp per unit |exposure change| per asset. Signals from the prior close. Gold before 2016-01-05 from Yahoo chart API (GLD_long_history.csv, overlap corr with GLD.csv 0.9957), SPY before that date from SPX price in spy_gex_daily.csv.
Reproduction of T3 arm 2 on CONFIRM: OK (this 0.859 / -17.08 vs T3 arm 2 0.853 / -17.082).

HOLDOUT Sharpe gain (3)-(1): -0.324. DD smaller: True. CI lower bound > -0.05: False. CONFIRM Sharpe higher: True.

Park vs redeploy (arm 5 minus arm 3):
  HOLDOUT: Sharpe 0.173 vs 0.026, max DD -21.41 vs -12.87, NW t +1.09, Sharpe diff CI [-0.108, +0.397]
  CONFIRM: Sharpe 1.042 vs 1.153, max DD -18.87 vs -13.00, NW t -1.07, Sharpe diff CI [-0.323, +0.083]

Comparisons (first arm vol-matched to second for the NW t; bootstrap 1000 x block 21, seed 42)

| window   | comparison    |   sharpe_diff |    nw_t |   ci_lo |   ci_hi |
|:---------|:--------------|--------------:|--------:|--------:|--------:|
| HOLDOUT  | (3) minus (1) |       -0.324  | -1.2606 | -0.8413 |  0.186  |
| HOLDOUT  | (5) minus (3) |        0.1475 |  1.0867 | -0.1085 |  0.3967 |
| HOLDOUT  | (5) minus (1) |       -0.1765 | -0.8192 | -0.585  |  0.2385 |
| HOLDOUT  | (4) minus (1) |       -0.5194 | -1.2863 | -1.3228 |  0.2216 |
| HOLDOUT  | (2) minus (1) |       -0.856  | -1.3521 | -2.0438 |  0.3856 |
| CONFIRM  | (3) minus (1) |        0.2936 |  1.2235 | -0.1849 |  0.8058 |
| CONFIRM  | (5) minus (3) |       -0.1112 | -1.0718 | -0.3228 |  0.083  |
| CONFIRM  | (5) minus (1) |        0.1824 |  0.7434 | -0.3129 |  0.7015 |
| CONFIRM  | (4) minus (1) |        0.4076 |  1.4656 | -0.1649 |  0.9862 |
| CONFIRM  | (2) minus (1) |       -0.0584 | -0.1306 | -0.8877 |  0.8658 |

| window   | arm                                      | period   |   ann_ret |   sharpe |   max_dd |   mean_gross |   frac_in |   trades_per_yr |   n_days |
|:---------|:-----------------------------------------|:---------|----------:|---------:|---------:|-------------:|----------:|----------------:|---------:|
| HOLDOUT  | 1 SPY 200dma x inv vol                   | full     |    4.2391 |   0.3498 | -18.7254 |       0.9874 |    0.8895 |        196.5    |     1176 |
| HOLDOUT  | 1 SPY 200dma x inv vol                   | H1       |   11.71   |   0.9301 |  -8.784  |       1.0895 |    0.983  |        222.429  |      588 |
| HOLDOUT  | 1 SPY 200dma x inv vol                   | H2       |   -3.2318 |  -0.2781 | -18.7254 |       0.8853 |    0.7959 |        170.571  |      588 |
| HOLDOUT  | 2 gold 200dma x inv vol                  | full     |   -3.8786 |  -0.5062 | -28.3309 |       0.2848 |    0.3206 |         84.8571 |     1176 |
| HOLDOUT  | 2 gold 200dma x inv vol                  | H1       |   -7.6711 |  -1.0941 | -23.8184 |       0.3069 |    0.3061 |         84      |      588 |
| HOLDOUT  | 2 gold 200dma x inv vol                  | H2       |   -0.0861 |  -0.0104 |  -8.4725 |       0.2627 |    0.335  |         85.7143 |      588 |
| HOLDOUT  | 3 SPY+gold trend N=2 (park)              | full     |    0.1802 |   0.0258 | -12.8665 |       0.6361 |    0.9209 |        281.143  |     1176 |
| HOLDOUT  | 3 SPY+gold trend N=2 (park)              | H1       |    2.0194 |   0.2715 |  -7.9327 |       0.6982 |    0.9949 |        306.429  |      588 |
| HOLDOUT  | 3 SPY+gold trend N=2 (park)              | H2       |   -1.6589 |  -0.2549 | -10.1427 |       0.574  |    0.8469 |        255.857  |      588 |
| HOLDOUT  | 4 SPY+gold always-long vol-sized         | full     |   -1.5558 |  -0.1697 | -19.3162 |       0.9566 |    1      |        472.286  |     1176 |
| HOLDOUT  | 4 SPY+gold always-long vol-sized         | H1       |    1.0052 |   0.1039 | -13.6382 |       0.9757 |    1      |        477      |      588 |
| HOLDOUT  | 4 SPY+gold always-long vol-sized         | H2       |   -4.1167 |  -0.4761 | -15.9075 |       0.9374 |    1      |        467.571  |      588 |
| HOLDOUT  | 5 SPY+gold trend renormalised (redeploy) | full     |    1.9314 |   0.1733 | -21.4133 |       0.9756 |    0.9209 |        282.643  |     1176 |
| HOLDOUT  | 5 SPY+gold trend renormalised (redeploy) | H1       |    6.6204 |   0.5682 |  -8.9713 |       1.0776 |    0.9949 |        308.571  |      588 |
| HOLDOUT  | 5 SPY+gold trend renormalised (redeploy) | H2       |   -2.7576 |  -0.2596 | -18.5569 |       0.8737 |    0.8469 |        256.714  |      588 |
| HOLDOUT  | leg corr (1 vs 2 trend P&L)              | full     |  nan      |  -0.056  | nan      |     nan      |  nan      |        nan      |     1176 |
| HOLDOUT  | leg corr (1 vs 2 trend P&L)              | H1       |  nan      |   0.0769 | nan      |     nan      |  nan      |        nan      |      588 |
| HOLDOUT  | leg corr (1 vs 2 trend P&L)              | H2       |  nan      |  -0.1768 | nan      |     nan      |  nan      |        nan      |      588 |
| CONFIRM  | 1 SPY 200dma x inv vol                   | full     |    9.9538 |   0.8592 | -17.0821 |       0.8909 |    0.8206 |        168.546  |     2464 |
| CONFIRM  | 1 SPY 200dma x inv vol                   | H1       |   12.0495 |   1.0129 | -17.0821 |       0.9969 |    0.8685 |        146.129  |     1133 |
| CONFIRM  | 1 SPY 200dma x inv vol                   | H2       |    8.1699 |   0.7218 | -13.497  |       0.8006 |    0.7799 |        187.627  |     1331 |
| CONFIRM  | 2 gold 200dma x inv vol                  | full     |    8.9914 |   0.8008 | -26.8316 |       0.6372 |    0.6932 |        170.796  |     2464 |
| CONFIRM  | 2 gold 200dma x inv vol                  | H1       |    7.4878 |   0.684  | -13.6634 |       0.7149 |    0.6752 |        158.362  |     1133 |
| CONFIRM  | 2 gold 200dma x inv vol                  | H2       |   10.2712 |   0.8958 | -18.0773 |       0.5711 |    0.7085 |        181.379  |     1331 |
| CONFIRM  | 3 SPY+gold trend N=2 (park)              | full     |    9.4726 |   1.1527 | -12.999  |       0.7641 |    0.9245 |        339.341  |     2464 |
| CONFIRM  | 3 SPY+gold trend N=2 (park)              | H1       |    9.7686 |   1.2473 |  -9.767  |       0.8559 |    0.9612 |        304.491  |     1133 |
| CONFIRM  | 3 SPY+gold trend N=2 (park)              | H2       |    9.2206 |   1.0804 | -12.999  |       0.6859 |    0.8933 |        369.007  |     1331 |
| CONFIRM  | 4 SPY+gold always-long vol-sized         | full     |   12.2389 |   1.2668 | -13.4324 |       0.9594 |    1      |        448.671  |     2464 |
| CONFIRM  | 4 SPY+gold always-long vol-sized         | H1       |   13.0889 |   1.4477 | -11.7344 |       1.0691 |    1      |        404.134  |     1133 |
| CONFIRM  | 4 SPY+gold always-long vol-sized         | H2       |   11.5153 |   1.1331 | -13.4324 |       0.866  |    1      |        486.582  |     1331 |
| CONFIRM  | 5 SPY+gold trend renormalised (redeploy) | full     |   10.9792 |   1.0416 | -18.8665 |       0.9396 |    0.9245 |        340.671  |     2464 |
| CONFIRM  | 5 SPY+gold trend renormalised (redeploy) | H1       |   11.3998 |   1.1167 | -14.7252 |       1.075  |    0.9612 |        306.715  |     1133 |
| CONFIRM  | 5 SPY+gold trend renormalised (redeploy) | H2       |   10.6212 |   0.9817 | -18.8665 |       0.8243 |    0.8933 |        369.575  |     1331 |
| CONFIRM  | leg corr (1 vs 2 trend P&L)              | full     |  nan      |   0.0377 | nan      |     nan      |  nan      |        nan      |     2464 |
| CONFIRM  | leg corr (1 vs 2 trend P&L)              | H1       |  nan      |  -0.0614 | nan      |     nan      |  nan      |        nan      |     1133 |
| CONFIRM  | leg corr (1 vs 2 trend P&L)              | H2       |  nan      |   0.1225 | nan      |     nan      |  nan      |        nan      |     1331 |
