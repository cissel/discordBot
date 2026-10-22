T3 multi-asset trend study (pre-registered)

Verdict: PARTIAL

Rule: PASS if basket TSMOM (arm 3) pooled Sharpe beats live SPY (arm 1) by >= 0.20 AND smaller max DD AND higher Sharpe in both halves; PARTIAL if pooled gain >= 0.20 but one half fails; FAIL otherwise.

Window 2016-12-29 to 2026-10-16, n = 2463 days. Cost 0.25bp per unit |exposure change| summed across assets.
Study 11 reproduction of arm 1: OK (ref Sharpe 0.856, DD -15.08).

Sharpe gain (3)-(1): +0.281. DD smaller: True. H1 higher: True. H2 higher: False.
NW(5) t of vol-matched daily P&L difference (3)-(1): +1.20. Paired block bootstrap (1000 x block 21) 95% CI on Sharpe difference: [-0.139, +0.787].

vs arm 1, 2 SPY 200dma x inv vol: NW t -0.03, Sharpe diff CI [-0.180, +0.171]
vs arm 1, 3b basket TSMOM (IEF for TLT): NW t +1.79, Sharpe diff CI [-0.016, +0.827]
vs arm 1, 4 basket always-long equal-risk: NW t +1.20, Sharpe diff CI [-0.185, +0.904]
vs arm 1, 5 basket TSMOM, SPY leg x GEX scalar: NW t +1.21, Sharpe diff CI [-0.160, +0.794]

| arm                                  | period   |   ann_ret |   sharpe |   max_dd |   mean_gross |   frac_in |   trades_per_yr |
|:-------------------------------------|:---------|----------:|---------:|---------:|-------------:|----------:|----------------:|
| 1 SPY 200dma x GEX scalar (live)     | pooled   |     8.412 |    0.856 |  -15.08  |        0.793 |     0.821 |         163.907 |
| 1 SPY 200dma x GEX scalar (live)     | H1       |     9.08  |    0.927 |  -14.121 |        0.853 |     0.868 |         151.467 |
| 1 SPY 200dma x GEX scalar (live)     | H2       |     7.843 |    0.796 |  -15.08  |        0.742 |     0.78  |         174.505 |
| 2 SPY 200dma x inv vol               | pooled   |     9.884 |    0.853 |  -17.082 |        0.891 |     0.821 |         168.512 |
| 2 SPY 200dma x inv vol               | H1       |    12.05  |    1.013 |  -17.082 |        0.997 |     0.868 |         146.129 |
| 2 SPY 200dma x inv vol               | H2       |     8.039 |    0.71  |  -13.497 |        0.8   |     0.78  |         187.579 |
| 3 basket TSMOM (SPY QQQ GLD TLT)     | pooled   |     7.69  |    1.137 |   -9.828 |        0.675 |     0.932 |         651.23  |
| 3 basket TSMOM (SPY QQQ GLD TLT)     | H1       |    10.658 |    1.581 |   -8.432 |        0.786 |     0.978 |         642.789 |
| 3 basket TSMOM (SPY QQQ GLD TLT)     | H2       |     5.162 |    0.761 |   -9.828 |        0.581 |     0.893 |         658.421 |
| 3b basket TSMOM (IEF for TLT)        | pooled   |     8.328 |    1.243 |  -10.126 |        0.754 |     0.937 |         564.263 |
| 3b basket TSMOM (IEF for TLT)        | H1       |    10.307 |    1.582 |   -6.762 |        0.84  |     0.989 |         521.349 |
| 3b basket TSMOM (IEF for TLT)        | H2       |     6.643 |    0.969 |  -10.126 |        0.681 |     0.893 |         600.821 |
| 4 basket always-long equal-risk      | pooled   |     9.836 |    1.185 |  -16.428 |        0.914 |     1     |         924.716 |
| 4 basket always-long equal-risk      | H1       |    13.257 |    1.751 |   -9.329 |        1.021 |     1     |         858.757 |
| 4 basket always-long equal-risk      | H2       |     6.923 |    0.781 |  -16.428 |        0.823 |     1     |         980.905 |
| 5 basket TSMOM, SPY leg x GEX scalar | pooled   |     7.593 |    1.148 |   -9.608 |        0.678 |     0.932 |         666.475 |
| 5 basket TSMOM, SPY leg x GEX scalar | H1       |    10.792 |    1.645 |   -8.233 |        0.798 |     0.978 |         666.588 |
| 5 basket TSMOM, SPY leg x GEX scalar | H2       |     4.867 |    0.732 |   -9.608 |        0.576 |     0.893 |         666.379 |
| standalone SPY 200dma x inv vol      | pooled   |     9.884 |    0.853 |  -17.082 |        0.891 |     0.821 |         168.512 |
| standalone SPY 200dma x inv vol      | H1       |    12.05  |    1.013 |  -17.082 |        0.997 |     0.868 |         146.129 |
| standalone SPY 200dma x inv vol      | H2       |     8.039 |    0.71  |  -13.497 |        0.8   |     0.78  |         187.579 |
| standalone QQQ 200dma x inv vol      | pooled   |    13.393 |    1.113 |  -13.13  |        0.669 |     0.821 |         197.16  |
| standalone QQQ 200dma x inv vol      | H1       |    16.175 |    1.268 |  -13.13  |        0.777 |     0.897 |         205.07  |
| standalone QQQ 200dma x inv vol      | H2       |    11.022 |    0.968 |   -9.788 |        0.578 |     0.756 |         190.421 |
| standalone GLD 200dma x inv vol      | pooled   |     8.995 |    0.801 |  -26.832 |        0.638 |     0.693 |         170.865 |
| standalone GLD 200dma x inv vol      | H1       |     7.488 |    0.684 |  -13.663 |        0.715 |     0.675 |         158.362 |
| standalone GLD 200dma x inv vol      | H2       |    10.279 |    0.896 |  -18.077 |        0.572 |     0.709 |         181.516 |
| standalone TLT 200dma x inv vol      | pooled   |    -1.51  |   -0.17  |  -44.72  |        0.502 |     0.476 |         115.104 |
| standalone TLT 200dma x inv vol      | H1       |     6.919 |    0.699 |  -11.235 |        0.653 |     0.594 |         133.673 |
| standalone TLT 200dma x inv vol      | H2       |    -8.691 |   -1.103 |  -38.838 |        0.373 |     0.376 |          99.284 |
| standalone IEF 200dma x inv vol      | pooled   |     1.042 |    0.163 |  -22.095 |        0.819 |     0.562 |          28.136 |
| standalone IEF 200dma x inv vol      | H1       |     5.514 |    0.933 |   -6.22  |        0.871 |     0.593 |          12.233 |
| standalone IEF 200dma x inv vol      | H2       |    -2.768 |   -0.409 |  -19.26  |        0.776 |     0.535 |          41.684 |

Correlation of standalone 200dma x inv vol P&L streams

|     |    SPY |    QQQ |   GLD |    TLT |
|:----|-------:|-------:|------:|-------:|
| SPY |  1     |  0.873 | 0.038 | -0.155 |
| QQQ |  0.873 |  1     | 0.039 | -0.131 |
| GLD |  0.038 |  0.039 | 1     |  0.221 |
| TLT | -0.155 | -0.131 | 0.221 |  1     |

With IEF in place of TLT

|     |    SPY |    QQQ |   GLD |    IEF |
|:----|-------:|-------:|------:|-------:|
| SPY |  1     |  0.873 | 0.038 | -0.118 |
| QQQ |  0.873 |  1     | 0.039 | -0.106 |
| GLD |  0.038 |  0.039 | 1     |  0.292 |
| IEF | -0.118 | -0.106 | 0.292 |  1     |
