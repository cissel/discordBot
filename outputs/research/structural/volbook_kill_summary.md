volBook kill-rule calibration (simulation, seed 42, 20,000 books per scenario)

Live rule: equity <= -4% of launch at any close, OR at exactly 25 closed: win rate < 60% AND net < 0.
Sizing 1 contract, equity $100k, max loss $390/contract, stop loss = -2x credit = $220/contract. The live sizer would use 2 contracts; equity-trigger numbers at that size are in the scenarios CSV.

Empirical transform
  source holdout_2011_2016_daily.csv (2011-05-02 to 2026-09-17, 3868 daily rows), stride 2, block bootstrap blocks of 5.
  per_credit = clip(pnl_vol * 0.5 * 0.2603, -2.20, +0.55); credit_scale is the smallest value with median winner = take (median winning pnl_vol 4.23 vol pts).
  resulting win rate 0.843, 0.422 of positions at the take, 0.027 at the stop, EV $+26 per contract.

Scenario table, live rule
                  scenario  win_rate  ev_usd_per_contract  p_killed_by_25  p_killed_by_40  p_killed_by_60  share_trigger_equity  share_trigger_count  median_pos_to_kill  surv_final_pnl_p5  surv_final_pnl_p50  surv_final_pnl_p95  all_books_final_pnl_p50  p_killed_by_60_at_live_size_2ct  share_trigger_equity_at_2ct
                 empirical     0.843               25.516           0.038           0.038           0.038                 0.000                0.038                25.0             28.317            1752.741            2712.458                 1708.858                            0.040                        0.003
    stylised p=0.85 (edge)     0.850               20.350           0.000           0.000           0.000                 0.000                0.000                25.0             67.502            1249.362            2274.677                 1248.890                            0.000                        0.000
    stylised p=0.78 (thin)     0.780                4.180           0.011           0.011           0.011                 0.000                0.011                25.0          -1031.073             302.333            1495.944                  290.533                            0.015                        0.004
 stylised p=0.70 (no edge)     0.700              -14.300           0.096           0.096           0.096                 0.000                0.096                25.0          -2096.612            -751.678             563.229                 -846.098                            0.175                        0.083
stylised p=0.62 (negative)     0.620              -32.780           0.338           0.338           0.340                 0.002                0.338                25.0          -3006.768           -1651.351            -303.434                -1949.637                            0.596                        0.291

False kill at p=0.85 by 40 closed: 0.000. Missed kill at p=0.70 by 40 closed: 0.904 (killed only 0.096).

Binomial standard error of a win rate: n=20: 0.080 at p=0.85, 0.102 at p=0.70, n=25: 0.071 at p=0.85, 0.092 at p=0.70, n=30: 0.065 at p=0.85, 0.084 at p=0.70, n=40: 0.056 at p=0.85, 0.072 at p=0.70

Grid: 192 rules. Rules meeting the pre-registered objective (false kill <= 5% AND kill-no-edge by N+15 >= 80%): 0.
None. Frontier (best achievable P(kill no-edge book by N+15) at each false-kill ceiling):
 false_kill_cap  best_kill_p70_by_N15  N  wr_thr     net_cond  eq_stop  false_kill_p85
           0.01                 0.423 40     0.7         none     0.03           0.004
           0.02                 0.427 25     0.7 net<-maxloss     0.03           0.020
           0.05                 0.492 25     0.7         none     0.03           0.026
           0.10                 0.492 25     0.7         none     0.03           0.026
           0.20                 0.492 25     0.7         none     0.03           0.026
           0.30                 0.492 25     0.7         none     0.03           0.026

Top 10 rules by kill-at-p70 with false kill <= 5%:
 N  wr_thr     net_cond eq_stop  false_kill_p85  kill_p70_by_N15  p62_killed_by_N15  emp_killed_by_N15
25     0.7         none    none           0.026            0.492              0.792              0.163
25     0.7         none    0.03           0.026            0.492              0.792              0.163
25     0.7         none    0.04           0.026            0.492              0.792              0.163
25     0.7         none    0.06           0.026            0.492              0.792              0.163
25     0.7        net<0    0.03           0.026            0.489              0.790              0.094
25     0.7        net<0    0.04           0.026            0.489              0.790              0.094
25     0.7        net<0    0.06           0.026            0.489              0.790              0.094
25     0.7        net<0    none           0.026            0.489              0.790              0.094
25     0.7 net<-maxloss    0.03           0.020            0.427              0.737              0.057
25     0.7 net<-maxloss    0.04           0.020            0.427              0.737              0.057

Top 10 rules by kill-at-p70 with false kill <= 10%:
 N  wr_thr     net_cond eq_stop  false_kill_p85  kill_p70_by_N15  p62_killed_by_N15  emp_killed_by_N15
25     0.7         none    none           0.026            0.492              0.792              0.163
25     0.7         none    0.03           0.026            0.492              0.792              0.163
25     0.7         none    0.04           0.026            0.492              0.792              0.163
25     0.7         none    0.06           0.026            0.492              0.792              0.163
25     0.7        net<0    0.03           0.026            0.489              0.790              0.094
25     0.7        net<0    0.04           0.026            0.489              0.790              0.094
25     0.7        net<0    0.06           0.026            0.489              0.790              0.094
25     0.7        net<0    none           0.026            0.489              0.790              0.094
25     0.7 net<-maxloss    0.03           0.020            0.427              0.737              0.057
25     0.7 net<-maxloss    0.04           0.020            0.427              0.737              0.057
