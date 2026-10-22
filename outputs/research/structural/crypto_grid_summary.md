Crypto grid study: pre-registered test of the live gridBot strategy

Pass rule (fixed before running): on the 12-month 1-minute window with the live halt policy, PASS if net P&L > 0, Sharpe > vol-matched buy-and-hold Sharpe, max drawdown < vol-matched buy-and-hold max drawdown, and all three hold in both halves. PARTIAL if net P&L > 0 but a benchmark condition fails. FAIL otherwise.

Verdicts
  BTC/USD: FAIL  (net P&L -182.37, Sharpe -0.97 vs B&H vol-matched -0.82, MDD 278 vs 145; H1 P&L -100.24 Sharpe -0.98 vs -1.75 MDD 278 vs 117; H2 P&L -82.13 Sharpe -0.96 vs 0.49 MDD 131 vs 100)
  ETH/USD: FAIL  (net P&L -565.06, Sharpe -2.12 vs B&H vol-matched -1.01, MDD 611 vs 255; H1 P&L -292.14 Sharpe -2.10 vs -1.80 MDD 432 vs 184; H2 P&L -272.92 Sharpe -2.13 vs 0.24 MDD 311 vs 163)
  SOL/USD: FAIL  (net P&L -14.50, Sharpe -0.06 vs B&H vol-matched -1.17, MDD 235 vs 269; H1 P&L -69.27 Sharpe -0.50 vs -2.42 MDD 169 vs 203; H2 P&L +54.77 Sharpe 0.45 vs 0.88 MDD 204 vs 174)
  BOOK: FAIL  (net P&L -761.93, Sharpe -1.20 vs B&H vol-matched -1.07, MDD 1039 vs 663; H1 P&L -461.65 Sharpe -1.36 vs -2.10 MDD 803 vs 601; H2 P&L -300.29 Sharpe -1.02 vs 0.60 MDD 582 vs 201)

Per symbol x window x policy
 symbol      window      policy  budget  days  days_in_market  round_trips  halts  rt_income_gross  maker_fees  taker_fees  halt_loss_gross  unrealized_end  total_pnl_net  total_pnl_pct  avg_inv_usd  max_inv_usd  max_inv_pct  mdd_usd  sharpe  nw_t  bh_vm_pnl  bh_vm_sharpe  bh_vm_mdd  bh_full_pnl  bh_full_sharpe  bh_full_mdd
BTC/USD    12m_1min        live  2500.0   366           349.0         1481     17          1145.42      824.47       20.30           488.44            5.41        -182.37          -7.29       289.10      1233.31        49.33   278.40   -0.97 -0.95     -72.03         -0.82     144.93      -629.98           -0.82      1267.48
BTC/USD    12m_1min pessimistic  2500.0   366           268.0         1227     14           908.72      671.60       18.01           277.02            1.28         -56.63          -2.27       259.19      1237.17        49.49   229.34   -0.31 -0.30     -66.13         -0.82     133.05      -629.98           -0.82      1267.48
BTC/USD 2021+_1hour        live  2500.0  2104          1948.0         3258    156          2872.19     1939.49      241.00          3707.10           12.63       -3002.76        -120.11       560.46      1246.17        49.85  3395.02   -1.27 -2.98     713.36          0.26    1008.39      3249.41            0.26      4593.24
BTC/USD 2021+_1hour pessimistic  2500.0  2104          1509.0         2173     85          1932.25     1224.02      126.51          1394.74            0.00        -813.02         -32.52       383.62      1233.43        49.34  1399.61   -0.48 -1.13     501.47          0.26     708.86      3249.41            0.26      4593.24
ETH/USD    12m_1min        live  2000.0   366           341.0         1357     25           865.95      599.27       30.61           801.08           -0.05        -565.06         -28.25       389.22       986.95        49.35   610.97   -2.12 -2.10    -150.71         -1.01     255.21      -769.65           -1.01      1303.31
ETH/USD    12m_1min pessimistic  2000.0   366           310.0          672      8           561.25      287.01       10.65           453.72            5.24        -184.88          -9.24       201.24       983.57        49.18   288.48   -0.92 -0.80     -81.39         -1.01     137.83      -769.65           -1.01      1303.31
ETH/USD 2021+_1hour        live  2000.0  2104          1948.0         4069    156          2978.31     1780.46      207.16          2598.29            1.66       -1605.93         -80.30       484.81      1030.86        51.54  2282.08   -0.66 -1.58     477.89          0.11    1484.73      2001.33            0.11      6217.88
ETH/USD 2021+_1hour pessimistic  2000.0  2104          1481.0         2180     89          1684.79      947.86      110.95          1051.38           17.60        -407.79         -20.39       323.79       986.21        49.31   964.40   -0.23 -0.55     323.52          0.11    1005.15      2001.33            0.11      6217.88
SOL/USD    12m_1min        live  1500.0   366           356.0          603     10           416.26      182.29        7.93           238.76           -1.78         -14.50          -0.97       367.25       689.05        45.94   235.08   -0.06 -0.06    -160.97         -1.17     268.93      -652.90           -1.17      1090.80
SOL/USD    12m_1min pessimistic  1500.0   366           331.0          477      5           384.13      142.79        4.51           238.60            3.89           2.11           0.14       213.82       688.72        45.91   178.71    0.01  0.01     -96.16         -1.17     160.65      -652.90           -1.17      1090.80
SOL/USD 2021+_1hour        live  1500.0  1686          1571.0         3309    115          2758.74     1033.62      108.63          1252.27           -2.33         361.90          24.13       332.39       759.99        50.67  1551.17    0.19  0.39    9826.34          0.18   23475.35     45198.10            0.18    107979.34
SOL/USD 2021+_1hour pessimistic  1500.0  1686          1252.0         1899     62          1871.27      584.78       55.06          -225.94           16.67        1474.04          98.27       237.97       698.82        46.59   588.34    0.95  1.96    7187.30          0.18   17170.64     45198.10            0.18    107979.34
   BOOK    12m_1min        live  6000.0   366             NaN         3441     52              NaN         NaN         NaN              NaN             NaN        -761.93         -12.70          NaN          NaN          NaN  1038.59   -1.20 -1.21    -383.71         -1.07     662.73     -2052.53           -1.03      3607.44

Decomposition: total = round_trip_income + fees + inventory + halt_losses (fees, inventory and halt terms signed as contributions)
 symbol      window      policy  round_trip_income     fees  inventory  halt_losses    total
BTC/USD    12m_1min        live            1145.42  -844.76       5.41      -488.44  -182.37
BTC/USD    12m_1min pessimistic             908.72  -689.61       1.28      -277.02   -56.63
BTC/USD 2021+_1hour        live            2872.19 -2180.48      12.63     -3707.10 -3002.76
BTC/USD 2021+_1hour pessimistic            1932.25 -1350.53       0.00     -1394.74  -813.02
ETH/USD    12m_1min        live             865.95  -629.88      -0.05      -801.08  -565.06
ETH/USD    12m_1min pessimistic             561.25  -297.66       5.24      -453.72  -184.88
ETH/USD 2021+_1hour        live            2978.31 -1987.61       1.66     -2598.29 -1605.93
ETH/USD 2021+_1hour pessimistic            1684.79 -1058.80      17.60     -1051.38  -407.79
SOL/USD    12m_1min        live             416.26  -190.22      -1.78      -238.76   -14.50
SOL/USD    12m_1min pessimistic             384.13  -147.31       3.89      -238.60     2.11
SOL/USD 2021+_1hour        live            2758.74 -1142.25      -2.33     -1252.27   361.90
SOL/USD 2021+_1hour pessimistic            1871.27  -639.84      16.67       225.94  1474.04

Sanity check vs real BTC fills log (Oct 15-20 2026)
  window: 2026-10-15 21:10:46+00:00 .. 2026-10-20 14:21:32.639790+00:00 (4.72 days)
  real_fill_orders_per_day: 20.99
  sim_fills_per_day: 12.72
  real_round_trips: 49
  real_round_trips_per_day: 10.39
  sim_round_trips: 30
  sim_round_trips_per_day: 6.36
  real_realized_gross_usd: 21.4
  real_usd_per_round_trip_gross: 0.437
  sim_realized_gross_usd: 30.29
  sim_usd_per_round_trip_gross: 1.01
  sim_fees_usd: 24.83
  sim_total_net_usd: 5.45
  sim_halts: 0
  real_sell_qty_mean: 0.001248
  sim_qty_per_level: 0.00338
  real_qty_sold: 0.08362
  sim_qty_sold: 0.1014
  real_gross_usd_per_day: 4.54
  sim_gross_usd_per_day: 6.42
  ratio_sim_over_real_round_trips_per_day: 0.61
  ratio_sim_over_real_usd_per_round_trip: 2.31
  ratio_sim_over_real_qty_sold: 1.21
  ratio_sim_over_real_gross_usd: 1.42
  within_2x_on_volume_and_gross_usd: True
  within_2x_on_per_trip_counts: False

Interpretation
  BTC/USD: round trips earned 1145 gross; fees took 845 (74% of gross), halts gave back 488, inventory at the end +5; net -182 on a 2500 budget vs -72 for holding the same average inventory (289 USD) and -630 for holding the full budget.
  ETH/USD: round trips earned 866 gross; fees took 630 (73% of gross), halts gave back 801, inventory at the end -0; net -565 on a 2000 budget vs -151 for holding the same average inventory (389 USD) and -770 for holding the full budget.
  SOL/USD: round trips earned 416 gross; fees took 190 (46% of gross), halts gave back 239, inventory at the end -2; net -14 on a 1500 budget vs -161 for holding the same average inventory (367 USD) and -653 for holding the full budget.
  The grid's gross round-trip income exceeds what inventory and halts give back only before fees; after the 0.30% maker round trip the residual per trip is a few cents and the breakout flattens, which always sell inventory bought on the way down, dominate. The grid is a long-crypto exposure (max inventory about half the budget, average 10-20%) with a negatively convex overlay: it sells the upside early and buys the downside until the kill switch realizes the loss.

Caveats: fills at the level price with no price improvement (slightly pessimistic on income); re-arms live from the next bar (matches the 1-minute cron); Alpaca paper partial fills are not modelled (the live log shows mean sell qty about a third of qty_per_level, which fragments round trips but does not change gross $ much, see sanity block); the 12-month window is a down year for all three coins, which is the regime where grids are supposed to shine relative to holding; the live reset policy assumes a next-UTC-day manual reset; 1Hour history is a lower bound on round trips and uses a conservative threshold flatten. 1Hour window is a lower bound on round trips (one fill per level per hour). ATR from completed UTC daily bars. Pessimistic policy waits 7 days after a halt.
