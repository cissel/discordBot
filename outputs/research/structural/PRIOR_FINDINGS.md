# Structural edge research - prior findings ledger (read before proposing any new test)

Purpose: every family of structural/flow-based SPY edge already tested in this
repo, with its verdict, so future candidate-generation (human or agent) does
NOT re-propose or re-run these. Source: outputs/research/structural/*summary.md,
compiled 2026-10-21.

| Family | Verdict | Note |
|---|---|---|
| Calendar effects (TOM, preFOMC, FOMC, OpExFri, OpExWeek, CPI, NFP, Mon-Fri) | FAIL | 0/12 stable both halves, Bonferroni kills everything, best \|t\| 1.9 |
| DIX deciles -> fwd return (5d, 21d) | FAIL | Spearman 0.07, not monotonic |
| GEX -> direction (long-unless-low-GEX) | FAIL | Sharpe 0.57 vs B&H 0.71 |
| GEX -> realized vol / RV-IV ratio | **PASS** (both halves) | Real but a vol/sizing signal, not direction. Already used in vol book GEX/term gate. |
| VIX9D>VIX3M inversion -> RV/implied ratio | **PASS** | Same bucket as GEX finding - term structure inversion is a vol regime signal |
| VIX9D>VIX3M inversion -> fwd return | FAIL | CI spans 0 |
| Overnight vs intraday return decomposition | FAIL | O/N Sharpe 0.92 < B&H 0.95 even at zero cost; breakeven cost 0bp |
| Block prints (dark pool) -> drift | FAIL | Data bug found+fixed (lookahead via stale market_price); still fails, 8/352 cells clear vs 35 expected by chance |
| Intraday momentum (first 30m -> last 30m) | FAIL | n=1451 days |
| Trend/vol book combo (T1 live combo, T2 knife-edge, C1 VRP-by-trend) | T1 FAIL, T2 PASS, C1 FAIL | T2 (knife-edge exit logic) is the only survivor, narrow |
| VRP conditional on GEX quintile + VIX term (Track A varswap) | **PASS** | Track B (short VXX / long SVXY) FAILs |
| Skew premium (C2, CBOE SKEW-style) | FAIL | Spearman all <0.11 |
| VRP cross-index (QQQ/VXN, IWM/RVX) | PARTIAL/FAIL | SPY-QQQ-IWM gated P&L corr >0.9, no extra diversification value |
| Multi-asset trend (TSMOM basket incl. gold) | PARTIAL | Gain >=0.20 pooled but fails one half |
| SPY + gold trend overlay (holdout-tested) | FAIL | Holdout Sharpe doesn't beat SPY-trend-x-invvol baseline |
| Liquidity/sentiment (AAII, fear&greed, Google Trends fear, net liquidity, HY OAS, CVD reversion) | 2/8 PASS | **Google Trends fear composite PASS** (t=-4.9, strongest finding in whole program, not yet live); **net liquidity 4w chg PASS** (t=2.4) but not tradeable as simple binary filter - worth continuous-sizing follow-up |
| Volbook mechanics (exit/stop optimization for the live iron-condor book) | FAIL | Stops don't beat hold-to-expiry on Sharpe+worst-month |
| RV forecast model (GARCH-style vs baseline) | PARTIAL-ECON | RMSE condition fails, econ condition (direction of vol change) passes |
| Horizon baseline: 200dma rule vs ML next-day model | **PASS for 200dma** | Dumb 200dma Sharpe 0.85 matches/beats ML Sharpe 0.72, NW t 0.13 - validates the live autopilot's simplicity |
| 5-day horizon ML vs 1-day | FAIL | Doesn't beat 1-day on pooled Sharpe or per-year count |
| Crypto grid bot (BTC/ETH/SOL/BOOK) | FAIL (all 4) | Bot killed Oct 19 2026 per ops log |
| Holdout 2011-2016 re-checks (GEX q1, inverted-term, complacent skew, post-dip) | ALL FAIL | None of the "live gate" ideas survive the earlier holdout |
| **Vol-control/risk-parity fund flow (RV21/RV63 ratio + RV21 level buckets)** | FAIL | Tested 2026-10-21, see vol_control_flow_summary.md. One weak signal (re-leveraging crossdown -> fwd5 tailwind, NW t=2.46, n=49) does NOT survive Bonferroni m=9 (bonf_p=0.12). Treat as noise until an independent holdout confirms it. |
| CFTC COT / TFF Leveraged-Funds net positioning in S&P 500 futures (level quintile continuation, weekly flow-change continuation, extreme-decile contrarian, sign persistence) | FAIL | Tested 2026-10-22, see cot_positioning_summary.md. n=707 weekly reports 2013-2026 (OFR TFF mirror, Leveraged Funds net_ratio = net/(long+short), signal date = report release_date to avoid lookahead). 0/10 cells pass. Best cell (net_ratio Q2 -> fwd4 excess) NW t=2.52 but bonf_p=0.12 (m=10) and half-split sign disagrees. Leveraged funds have been persistently net SHORT SPX futures the whole sample (hedging flow, not a clean directional bet) - consistent with literature's mixed/unstable findings for this family. |
| **Cboe Implied Correlation (COR1M/COR3M) level, 5d change, term slope, dispersion premium vs realized sector correlation** | FAIL (at backtest stage) | Hypothesis test (2026-10-23, implied_correlation_dispersion_summary.md): 1/16 cells PASS, lowest COR3M quintile -> fwd21d realized-vol excess -2.72 vol pts, NW t=-4.87, bonf_p=1.8e-05, both halves same sign. WFCV strategy backtest (2026-10-22/23, implied_correlation_dispersion_WFCV_summary.md): added a walk-forward (no-lookahead) COR3M bottom-quintile gate on top of the already-live GEX/term short-variance gate. Worst month shrank from -111 to -17 vol pts and NW t rose to 2.90, but eligible days collapsed from 77% to 32% of the sample and monthly Sharpe got WORSE (1.82 -> 0.76), so it fails the pass rule overall. Closed - do not re-propose, do not wire into vol book. |

## Open mechanism categories NOT yet tested (candidate pool - see EDGE_CANDIDATES_QUEUE.md)
buyback blackout seasonality, Treasury auction cycle, FX/dollar regime,
margin debt / leverage cycle, factor rotation (value/growth/size) regime
signal, economic surprise index, put/call ratio extremes (distinct from
DIX/GEX), insider transaction aggregates. (COT/futures positioning and
implied-correlation/dispersion now closed - see table above.)

## Standing statistical discipline for ANY new study (do not deviate)
- Pre-register hypotheses in the script docstring BEFORE computing results.
- Newey-West t-stat (Bartlett kernel, 5 lags) for all mean-difference tests.
- Report full sample + chronological half-split; require SAME SIGN in both
  halves to count as real, not just full-sample significance.
- Bonferroni-correct across every cell in the hypothesis family (m = number of
  testable cells), using `calendar_effects.py` as the template.
- PASS requires |NW t| >= 2.0 full sample, Bonferroni-survives, AND both
  halves agree in sign. PARTIAL = clears t but fails Bonferroni or one half.
  FAIL otherwise.
- Prefer a holdout window (pre-2016 or similarly out-of-sample) for anything
  that PASSes the half-split test before calling it real.
- Write `<name>_summary.md` + `<name>_results.csv` in outputs/research/structural/.
- A pure hypothesis test does not need an R navy-theme plot. A strategy
  BACKTEST (has an equity curve / Sharpe / drawdown) ALWAYS needs one -
  James's standing rule, navy #02233F background, white text everywhere.
- Nothing gets committed to git. Nothing gets wired into the live autopilot
  or vol book without an explicit PASS at the strategy-backtest stage AND
  James's sign-off - flag it in #test-env and stop, do not auto-integrate.
