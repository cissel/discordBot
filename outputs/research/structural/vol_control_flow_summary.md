# Vol-control / risk-parity fund flow mechanics (SPY, 2016-2026)

Proxy: RV21/RV63 ratio and RV21 level buckets, from existing spy_vol_r21/r63 features (n=2652 days). Pre-registered H1-H4, see script docstring. Pass rule: |NW t| >= 2.0 on full sample AND sign agrees both halves (or directional check for the two non-parametric tail hypotheses).

Result: 0 PASS, 0 PARTIAL, 10 FAIL out of 11 cells (9 had a computable NW t-stat).

| hyp                            |    n |         mean |        nw_t |       mean_H1 |       mean_H2 |        t_H1 |         t_H2 | sign_agree   | family     | note                         |       raw_p |     bonf_p | verdict                        |
|:-------------------------------|-----:|-------------:|------------:|--------------:|--------------:|------------:|-------------:|:-------------|:-----------|:-----------------------------|------------:|-----------:|:-------------------------------|
| H1_RVbucket_lt8_fwd5excess     |  475 | -0.00156455  |  -1.43453   |  -0.000991912 |  -0.00499194  |  -0.845151  |  -1.79762    | True         | H1_level   | nan                          |   0.15142   |   1        | FAIL                           |
| H1_RVbucket_lt8_fwd21excess    |  475 | -0.00550111  |  -1.83626   |  -0.00351028  |  -0.0174168   |  -1.07938   |  -2.58225    | True         | H1_level   | nan                          |   0.0663193 |   0.596874 | FAIL                           |
| H1_RVbucket_8to15_fwd5excess   | 1284 |  0.000209765 |   0.247171  |   0.000377025 |   9.33047e-05 |   0.277268  |   0.0860731  | True         | H1_level   | nan                          |   0.804776  |   1        | FAIL                           |
| H1_RVbucket_8to15_fwd21excess  | 1284 |  9.97509e-05 |   0.0393618 |  -0.00279434  |   0.00215856  |  -0.581566  |   0.813402   | False        | H1_level   | nan                          |   0.968602  |   1        | FAIL                           |
| H1_RVbucket_15to20_fwd5excess  |  407 |  0.000480737 |   0.252249  |  -0.000199375 |   0.000869373 |  -0.0723891 |   0.343159   | False        | H1_level   | nan                          |   0.800849  |   1        | FAIL                           |
| H1_RVbucket_15to20_fwd21excess |  407 | -0.00321804  |  -0.601227  |   0.00719404  |  -0.0091678   |   1.1211    |  -1.24039    | False        | H1_level   | nan                          |   0.547689  |   1        | FAIL                           |
| H1_RVbucket_gt20_fwd5excess    |  486 |  0.000574506 |   0.179455  |   0.00301821  |  -0.00193029  |   0.607212  |  -0.486394   | False        | H1_level   | nan                          |   0.85758   |   1        | FAIL                           |
| H1_RVbucket_gt20_fwd21excess   |  486 |  0.00781232  |   1.24041   |   0.0154782   |  -4.52583e-05 |   1.71148   |  -0.00526088 | False        | H1_level   | nan                          |   0.214824  |   1        | FAIL                           |
| H3_crossdn_fwd5excess          |   49 |  0.00396663  |   2.46464   |   0.00279271  |   0.0059884   |   1.53358   |   2.05171    | True         | H3_crossdn | nan                          |   0.0137152 |   0.123437 | PARTIAL (fails Bonferroni m=9) |
| H3_fragility_p5_fwd21          |   49 | -0.041106    | nan         | nan           | nan           | nan         | nan          | False        | H3_crossdn | unconditional_p5=-0.0628     | nan         | nan        | FAIL                           |
| H4_coiled_spring_semidev       |  154 |  0.0154296   | nan         | nan           | nan           | nan         | nan          | False        | H4_coiled  | unconditional_semidev=0.0323 | nan         | nan        | FAIL                           |

OVERALL VERDICT: FAIL. No cell clears the pre-registered bar. The RV21/RV63 ratio and level thresholds described in vol-control-fund literature do not show a stable, both-halves-agreeing forward-return or tail-risk signature in SPY 2016-2026 at daily resolution. Consistent with the existing GEX finding: realized-vol regime variables are good vol/sizing signals but keep failing as directional or forward-risk timing signals once tested with this repo's half-split + NW-t bar.
