# RV forecast model vs GEX lookup baseline (2026-10-19)

Rows: 2560 complete target rows 2016-06-24 .. 2026-09-17; walk-forward yearly refits 2018-2026, pooled OOS n=2177.

Pooled metrics (log RV_fwd):

```
               n    rmse     mae  oos_r2    bias  mz_slope  rmse_vs_baseline_pct  dm_t_vs_baseline
model                                                                                             
baseline    2177  0.4105  0.3115  0.5155 -0.0254    0.6523                   NaN               NaN
vix_only    2177  0.4746  0.3941  0.3521  0.2797    0.9122               15.6392            2.7595
lookup_wf   2177  0.4163  0.3079  0.5017 -0.0133    0.7142                1.4193            0.4047
har         2177  0.4344  0.3161  0.4575 -0.0877    0.6616                5.8220            1.2637
har_struct  2177  0.4426  0.3188  0.4366 -0.1116    0.6538                7.8403            1.4397
ridge       2177  0.4525  0.3266  0.4112 -0.1509    0.7025               10.2431            1.7403
gbm         2177  0.4713  0.3435  0.3611 -0.1554    0.6204               14.8346            2.5663
```

Economic sign test:

```
            eligible_frac  n_elig  mean_pnl_volpts  hit_rate  monthly_sharpe  worst_month_volpts  nw_t_gated
model                                                                                                       
baseline            0.439   955.0            1.225     0.841           0.633             -78.512       0.782
lookup_wf           0.435   948.0            2.390     0.799           0.063             -23.611       2.974
har                 0.405   881.0            0.944     0.778           0.707             -45.581       0.828
har_struct          0.493  1073.0            2.201     0.801           0.950             -34.861       2.559
ridge               0.626  1362.0            2.006     0.801           0.533             -79.194       1.534
gbm                 0.635  1383.0            1.537     0.800           0.420             -77.921       1.149
```

Per-year RMSE:

```
model  baseline  vix_only  lookup_wf     har  har_struct   ridge     gbm
year                                                                    
2018     0.5363    0.4927     0.6553  0.7433      0.7989  0.8022  0.7473
2019     0.4054    0.5355     0.3815  0.3727      0.3834  0.3747  0.4356
2020     0.6062    0.6352     0.5783  0.6065      0.5798  0.6349  0.7394
2021     0.3705    0.5372     0.3452  0.3120      0.2935  0.3041  0.2994
2022     0.2916    0.2285     0.3364  0.3611      0.3982  0.4038  0.4400
2023     0.2408    0.3513     0.1974  0.1734      0.1709  0.1704  0.1826
2024     0.3901    0.3935     0.3056  0.3198      0.3129  0.3133  0.3206
2025     0.3967    0.5356     0.4319  0.4285      0.4154  0.4153  0.4161
2026     0.2779    0.4164     0.2518  0.2222      0.2226  0.2225  0.2386
```

Years beating baseline: {'lookup_wf': 6, 'har': 5, 'har_struct': 6, 'ridge': 5, 'gbm': 4}

Ratio form:

```
            mean_pred_ratio  mean_actual_ratio  rmse_ratio  rmse_volpts
model                                                                  
baseline             0.7486             0.8237      0.4559       8.5172
vix_only             1.0000             0.8237      0.4711       9.2231
lookup_wf            0.7517             0.8237      0.4589       8.6688
har                  0.6981             0.8237      0.4716       8.8772
har_struct           0.6852             0.8237      0.4728       8.7909
ridge                0.6602             0.8237      0.4801       9.3234
gbm                  0.6595             0.8237      0.4903      10.0362
```

har_struct coefficients (train through 2025):

```
              coef  coef_x_sd
log_rv1    -0.0034    -0.0037
log_rv5     0.0138     0.0090
log_rv22   -0.0157    -0.0081
log_vix     1.1190     0.3792
gq2        -0.0845    -0.0306
gq3        -0.1004    -0.0382
gq4        -0.0521    -0.0206
gq5        -0.0403    -0.0184
term_slope -0.2360    -0.0424
inverted    0.0582     0.0195
log_vvix   -0.5298    -0.0816
```

Sub-sample 2022-2026 intraday check:

```
                     n    rmse     mae  oos_r2    bias  mz_slope  rmse_vs_baseline_pct  dm_t_vs_baseline
model                                                                                                   
baseline          1177  0.3284  0.2555  0.5683 -0.0501    0.6961                   NaN               NaN
vix_only          1177  0.3963  0.3301  0.3714  0.2567    1.0688               20.6664            2.5288
lookup_wf         1177  0.3707  0.2869  0.4500 -0.0521    0.8052               12.8716            1.8435
har_struct        1177  0.4256  0.3181  0.2751 -0.1214    0.5019               29.5782            2.3691
har_struct_intra  1177  0.4349  0.3231  0.2431 -0.1203    0.4293               32.4096            2.4508
```

Best non-baseline: har. RMSE 0.4344 vs baseline 0.4105 (+5.8%), wins 5/9, DM t 1.26. Econ Sharpe 0.707 vs baseline 0.633.

Condition 1 (RMSE): False. Condition 2 (econ): True.

## VERDICT: PARTIAL-ECON

No model saved; baseline lookup stands.
