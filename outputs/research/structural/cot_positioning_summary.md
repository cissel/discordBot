# CFTC TFF Leveraged-Funds S&P 500 positioning vs forward SPX return (2013-2026, n=707 weekly reports)

Data: OFR Hedge Fund Monitor mirror of CFTC Traders-in-Financial-Futures, Leveraged Funds long/net/short notional in CME S&P 500 futures. Signal date = report release_date (Friday, +3 cal days after as-of Tuesday), never the as-of date, to avoid lookahead. Price series: SPX (spy_gex_daily.csv).

Pre-registered H1 (level quintile continuation), H2 (weekly flow-change continuation), H3 (extreme-decile contrarian), H4 (sign persistence). Pass rule: |NW t| >= 2.0 full sample AND Bonferroni-survives (m=10 cells) AND sign agrees both chronological halves.

Result: 0 PASS, 1 PARTIAL, 9 FAIL out of 10 cells (10 had a computable NW t-stat).

| hyp                                 | family     |   n |         mean |      nw_t |       mean_H1 |      mean_H2 |         t_H1 |        t_H2 | sign_agree   |   bonf_p | verdict                             |
|:------------------------------------|:-----------|----:|-------------:|----------:|--------------:|-------------:|-------------:|------------:|:-------------|---------:|:------------------------------------|
| H1_netratio_Q1_mostshort_fwd4excess | H1_level   | 142 | -0.00487618  | -0.952855 | nan           | -0.00487618  | nan          | -0.952855   | False        | 1        | FAIL                                |
| H1_netratio_Q2_fwd4excess           | H1_level   | 141 |  0.0117698   |  2.52242  |  -0.00093438  |  0.0177248   |  -0.156831   |  3.07454    | False        | 0.11655  | PARTIAL (half-split sign disagrees) |
| H1_netratio_Q3_fwd4excess           | H1_level   | 141 | -0.00738943  | -1.1587   |  -0.00452889  | -0.0131105   |  -0.931267   | -0.803134   | True         | 1        | FAIL                                |
| H1_netratio_Q4_fwd4excess           | H1_level   | 141 | -0.000633348 | -0.144556 |  -0.00274698  |  0.00789665  |  -0.702399   |  0.541405   | False        | 1        | FAIL                                |
| H1_netratio_Q5_mostlong_fwd4excess  | H1_level   | 142 |  0.00115558  |  0.253198 |   0.00174695  | -0.000301211 |   0.41757    | -0.0255519  | False        | 1        | FAIL                                |
| H2_flow_D1_sell_fwd1excess          | H2_flow    | 142 | -0.00126438  | -0.536982 |  -9.61886e-06 | -0.00226501  |  -0.00498647 | -0.575554   | True         | 1        | FAIL                                |
| H2_flow_D5_buy_fwd1excess           | H2_flow    | 141 | -0.000232486 | -0.119856 |  -0.000310687 | -0.000172858 |  -0.135222   | -0.0588744  | True         | 1        | FAIL                                |
| H3_extremeshort_decile_fwd4excess   | H3_extreme |  71 | -0.013922    | -1.85196  | nan           | -0.013922    | nan          | -1.85196    | False        | 0.640318 | FAIL                                |
| H3_extremelong_decile_fwd4excess    | H3_extreme |  71 |  0.00206823  |  0.272519 |   0.000340039 |  0.00443008  |   0.0447372  |  0.294509   | True         | 1        | FAIL                                |
| H4_netshort_sign_fwd1excess         | H4_sign    | 692 | -0.000152926 | -0.193412 |  -0.000292523 | -1.25193e-05 |  -0.344665   | -0.00936863 | True         | 1        | FAIL                                |

Strongest cell by |NW t|: H1_netratio_Q2_fwd4excess (nw_t=2.52, bonf_p=0.117, n=141, verdict=PARTIAL (half-split sign disagrees))

OVERALL VERDICT: FAIL. No cell clears the pre-registered bar (|NW t|>=2.0 + Bonferroni + both-halves sign agreement). Consistent with the mixed/unstable literature (CXO Advisory, Wang 2000) this candidate was queued to re-check: CFTC leveraged-fund positioning in SPX futures does not show a stable forward-return signature in this repo's 2013-2026 sample once corrected for multiple testing. Closed - do not re-propose this family.
