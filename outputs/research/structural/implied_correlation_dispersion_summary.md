# Cboe Implied Correlation (COR1M/COR3M) vs forward SPX return / realized vol / dispersion premium (n=3890 trading days, 2011-05-02 to 2026-10-19)

Data: Cboe COR1M/COR3M free CDN history (fetchImpliedCorr.py), SPX price (spy_gex_daily.csv), VIX (VIX.csv), realized correlation of 9 core SPDR sector ETFs (sector_bars_historical.csv, 2016+, H4 only). Signal known at close of day t, forward windows start t+1 (no lookahead).

Pre-registered H1 (COR3M level quintile -> fwd21 realized-vol continuation), H2 (COR3M 5d change, top/bottom quintile -> fwd5 return reversion), H3 (COR1M>COR3M term inversion -> fwd5 return and fwd21 RV), H4 (dispersion premium = COR3M - 100*realized_corr21, quintile -> fwd21 short-variance P&L, 2016+ only). Pass rule: |NW t| >= 2.0 full sample AND Bonferroni-survives (m=16 cells) AND sign agrees both chronological halves.

Result: 1 PASS, 3 PARTIAL, 12 FAIL out of 16 cells (16 had a computable NW t-stat).

| hyp                                | family                |    n |         mean |      nw_t |      mean_H1 |      mean_H2 |       t_H1 |      t_H2 | sign_agree   |      bonf_p | verdict                             |
|:-----------------------------------|:----------------------|-----:|-------------:|----------:|-------------:|-------------:|-----------:|----------:|:-------------|------------:|:------------------------------------|
| H1_cor3m_Q1_low_fwd21rvexcess      | H1_level_vol          |  774 | -2.71688     | -4.86774  | -4.37679     | -2.49847     | -1.58217   | -4.9455   | True         | 1.80608e-05 | PASS                                |
| H1_cor3m_Q2_fwd21rvexcess          | H1_level_vol          |  771 | -1.06869     | -0.834205 | -5.32308     |  1.27679     | -4.72904   |  0.754305 | False        | 1           | FAIL                                |
| H1_cor3m_Q3_fwd21rvexcess          | H1_level_vol          |  772 | -0.993074    | -1.18291  | -1.77037     | -0.139739    | -1.48591   | -0.122061 | True         | 1           | FAIL                                |
| H1_cor3m_Q4_fwd21rvexcess          | H1_level_vol          |  772 |  0.187459    |  0.201136 | -2.7243      |  6.72058     | -4.79906   |  4.7912   | False        | 1           | FAIL                                |
| H1_cor3m_Q5_high_fwd21rvexcess     | H1_level_vol          |  773 |  4.60241     |  2.45009  |  1.61805     | 18.8338      |  1.14555   |  2.80928  | True         | 0.228512    | PARTIAL (fails Bonferroni m=16)     |
| H2_corchg_D1_down_fwd5excess       | H2_spike_reversion    |  774 |  0.00146949  |  1.4583   | -0.000740557 |  0.0041468   | -0.673167  |  2.4185   | False        | 1           | FAIL                                |
| H2_corchg_D5_up_fwd5excess         | H2_spike_reversion    |  773 |  0.00118143  |  0.756115 |  0.00255638  | -0.000429119 |  1.69707   | -0.148893 | False        | 1           | FAIL                                |
| H3_terminverted_fwd5excess         | H3_term_slope         |  904 |  0.000957283 |  0.585657 | -4.04569e-05 |  0.00172809  | -0.018453  |  0.73625  | False        | 1           | FAIL                                |
| H3_termnormal_fwd5excess           | H3_term_slope         | 2980 | -0.000290397 | -0.458385 | -0.000726726 |  0.000183182 | -0.937833  |  0.180006 | False        | 1           | FAIL                                |
| H3_terminverted_fwd21rvexcess      | H3_term_slope         |  904 |  4.13502     |  2.56781  |  3.50447     |  4.62215     |  1.9732    |  1.84623  | True         | 0.163747    | PARTIAL (fails Bonferroni m=16)     |
| H3_termnormal_fwd21rvexcess        | H3_term_slope         | 2964 | -1.26115     | -2.27085  | -2.815       |  0.444448    | -4.71378   |  0.485176 | False        | 0.3705      | PARTIAL (half-split sign disagrees) |
| H4_dispprem_Q1_low_fwd21vrpexcess  | H4_dispersion_premium |  533 | -0.00326275  | -0.128039 |  0.0126558   | -0.00649676  |  0.25233   | -0.224987 | False        | 1           | FAIL                                |
| H4_dispprem_Q2_fwd21vrpexcess      | H4_dispersion_premium |  533 | -0.0133154   | -0.343849 |  0.0288221   | -0.0235305   |  0.641533  | -0.503439 | False        | 1           | FAIL                                |
| H4_dispprem_Q3_fwd21vrpexcess      | H4_dispersion_premium |  533 |  0.0268615   |  1.51198  | -0.00180741  |  0.0359223   | -0.0431129 |  1.87352  | False        | 1           | FAIL                                |
| H4_dispprem_Q4_fwd21vrpexcess      | H4_dispersion_premium |  533 | -0.00564     | -0.280343 |  0.00303854  | -0.00953119  |  0.0605223 | -0.512746 | False        | 1           | FAIL                                |
| H4_dispprem_Q5_high_fwd21vrpexcess | H4_dispersion_premium |  533 |  0.0197906   |  0.951271 |  0.0459315   | -0.00473424  |  1.57016   | -0.186908 | False        | 1           | FAIL                                |

Strongest cell by |NW t|: H1_cor3m_Q1_low_fwd21rvexcess (nw_t=-4.87, bonf_p=0.000, n=774, verdict=PASS)

OVERALL VERDICT: 1 cell(s) cleared the full bar - see table above. Move the surviving hypothesis id(s) to status=backtest_strategy in the queue for a WFCV strategy backtest before any further action.
