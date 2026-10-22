#!/usr/bin/env python3
"""
implied_correlation_dispersion_WFCV.py  (pre-registered, Oct 23 2026)
======================================================================
Strategy-backtest stage for the implied_correlation_dispersion candidate
(queue id, status=backtest_strategy). The pre-registered hypothesis test
(implied_correlation_dispersion_study.py) found ONE surviving cell out of 16:
lowest COR3M quintile -> lower forward-21d realized SPX vol (NW t=-4.87,
bonf_p=1.8e-05, both halves agree). That is a vol-REGIME signal, same
character as the already-live GEX-quintile and VIX9D/VIX3M-term gates used
in the vol book's short-variance strategy (vrp_regime_study.py, Track A
PASS). This script asks the only question that matters for actually using
it: does adding a COR3M-low walk-forward gate on TOP of the existing live
gate improve the short-variance strategy, out of sample, with zero lookahead?

CRITICAL METHODOLOGY FIX vs the hypothesis-test stage: the original study
used a full-sample pd.qcut to define the COR3M quintile, which has lookahead
(the quintile cutoffs are computed using future data). This script replaces
that with a true walk-forward measure: cor3m_pctile_wf = percentile rank of
today's COR3M within a TRAILING 756-trading-day (~3y) window only, so the
gate at any date t only uses information available by date t. This mirrors
the rolling(252) walk-forward construction vrp_regime_study.py already uses
for gex_q.

BASELINE  (current live vol book gate, reproduced exactly from
          vrp_regime_study.py's gate_both): not-GEX-quintile-1 AND
          VIX9D<=VIX3M (not inverted). Both lagged 1 day exactly as in that
          script.
PROPOSED  BASELINE gate AND cor3m_pctile_wf <= 0.20 (walk-forward bottom
          quintile of COR3M, trailing 756d window, min_periods=252 so the
          gate only turns on after 1y of COR3M history exists).

Instrument: same synthetic 30-day variance swap as vrp_regime_study Track A
(frictionless mechanism-level P&L is that script's established convention;
this script additionally charges a conservative regime-switch cost of 0.05
vol points of vega-notional every time the gate flips on/off, as a stand-in
for the real bid/ask on rolling the SPY iron-condor position James actually
trades -- a 30d iron condor's open/close round-trip is on the order of a few
cents of vol, 0.05 vol pts is a deliberately conservative free-lunch check).

PASS RULE (strategy-backtest stage, all must hold vs BASELINE):
  (a) PROPOSED monthly Sharpe (non-overlapping, 3-offset avg) > BASELINE
  (b) PROPOSED worst calendar-month P&L (vol pts) not worse in magnitude
      than BASELINE's worst month
  (c) PROPOSED mean P&L > 0 in BOTH chronological halves
  (d) PROPOSED NW t-stat (21 lags) >= 2.0
  (e) PROPOSED mean P&L > 0 in a held-out final-20%-of-sample window (never
      touched by any threshold calibration -- there is none here since the
      756d window and 20th-percentile cutoff are fixed constants reused
      from vrp_regime_study's existing conventions, not fit to this data)
If ALL hold: candidate -> READY_FOR_JAMES_REVIEW (hard stop, James decides).
Otherwise: done_fail at the backtest stage -- the hypothesis-test PASS does
not translate into an improvement over what is already live.

Outputs:
  outputs/research/structural/implied_correlation_dispersion_WFCV_results.csv
  outputs/research/structural/implied_correlation_dispersion_WFCV_daily.csv
  outputs/research/structural/implied_correlation_dispersion_WFCV_summary.md
  outputs/research/structural/implied_correlation_dispersion_WFCV.png (R, navy theme)

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/implied_correlation_dispersion_WFCV.py
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/discordBot")
CACHE = os.path.join(BASE, "outputs/markets/cache")
OUT = os.path.join(BASE, "outputs/research/structural")
H = 21
COST_PER_FLIP = 0.05   # vol points, charged whenever the gate state changes


def nw_t(x, lags=H):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return np.nan
    m = x.mean()
    u = x - m
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    se = np.sqrt(max(s, 0) / n)
    return float(m / se) if se > 0 else np.nan


def load():
    spy = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"])
    spy = spy[["date", "price", "gex"]].dropna(subset=["price"]).sort_values("date").drop_duplicates("date")
    spy["ret"] = spy["price"].pct_change()

    vix = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"]).rename(columns={"VIXCLS_val": "vix"})
    vt = pd.read_csv(os.path.join(CACHE, "vix_term_history.csv"), parse_dates=["date"])[["date", "vix9d", "vix3m"]]
    corr = pd.read_csv(os.path.join(CACHE, "implied_corr_daily.csv"), parse_dates=["date"])[["date", "cor3m"]]

    d = spy.merge(vix, on="date", how="inner").merge(vt, on="date", how="inner").merge(corr, on="date", how="inner")
    d = d.sort_values("date").reset_index(drop=True)

    # forward realized variance over t+1..t+21 (vol pts^2, annualised), no lookahead
    n = len(d)
    lr = np.log1p(d["ret"])
    rv_fwd_var = np.full(n, np.nan)
    for i in range(n):
        if i + 22 <= n - 1:
            w = lr.values[i + 1:i + 22]
            rv_fwd_var[i] = np.nanmean(w ** 2) * 252 * 1e4
    d["rv_fwd_var"] = rv_fwd_var

    # existing LIVE gate, reproduced exactly from vrp_regime_study.py
    gex = d.set_index("date")["gex"]
    gq = gex.rolling(252, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    d["gex_q"] = (np.floor((gq.values * 5).clip(0, 4.999)) + 1)
    d["gex_q"] = pd.Series(d["gex_q"]).shift(1).values
    inv = (d["vix9d"] > d["vix3m"]).astype(float)
    d["inverted"] = inv.shift(1).values
    d["gate_live"] = (d["gex_q"] != 1) & (d["inverted"] == 0)

    # NEW: walk-forward COR3M percentile, trailing 756d window, no lookahead
    cor_pct = d["cor3m"].rolling(756, min_periods=252).apply(
        lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else np.nan, raw=True)
    d["cor3m_pctile_wf"] = cor_pct.values
    d["gate_cor_low"] = d["cor3m_pctile_wf"] <= 0.20
    d["gate_proposed"] = d["gate_live"] & d["gate_cor_low"]

    d = d.dropna(subset=["gex_q", "inverted", "cor3m_pctile_wf", "rv_fwd_var", "vix"]).reset_index(drop=True)
    d["half"] = np.where(d["date"] < d["date"].iloc[len(d) // 2], "H1", "H2")
    holdout_start = d["date"].iloc[int(len(d) * 0.80)]
    d["holdout"] = d["date"] >= holdout_start
    return d, holdout_start


def pnl_for_gate(d, gatecol):
    K = d["vix"] ** 2
    pnl_var = K - d["rv_fwd_var"]
    pnl_vol = pnl_var / (2 * d["vix"])
    gate = d[gatecol].astype(bool)
    s = pnl_vol.where(gate, 0.0).copy()
    flips = gate.astype(int).diff().abs().fillna(0.0)
    s = s - flips * COST_PER_FLIP
    return s, gate


def stats_for(label, s, gate, d, holdout_start):
    act = s[gate]
    mo = [s.iloc[k::H] for k in range(3)]
    mo_sharpe = np.mean([m.mean() / m.std() * np.sqrt(12) for m in mo if m.std() > 0])
    cal = s.groupby(d["date"].dt.to_period("M")).mean()
    mo_worst = cal.min()
    h1 = act[d.loc[act.index, "half"] == "H1"].mean()
    h2 = act[d.loc[act.index, "half"] == "H2"].mean()
    hold = act[d.loc[act.index, "holdout"]].mean()
    n_hold = int(d.loc[act.index, "holdout"].sum())
    return {
        "label": label, "eligible_frac": round(float(gate.mean()), 3), "n_days": int(gate.sum()),
        "mean_volpts": round(float(act.mean()), 4), "hit_rate": round(float((act > 0).mean()), 3),
        "monthly_sharpe": round(float(mo_sharpe), 3), "worst_month_volpts": round(float(mo_worst), 2),
        "nw_t": round(nw_t(s.values), 2), "mean_H1": round(float(h1), 3), "mean_H2": round(float(h2), 3),
        "mean_holdout": round(float(hold), 3), "n_holdout": n_hold,
    }


def main():
    d, holdout_start = load()
    print(f"[implied_correlation_dispersion_WFCV] {len(d)} days, {d['date'].min().date()} -> {d['date'].max().date()}")
    print(f"  holdout window starts {holdout_start.date()} (final 20% of sample, n={int(d['holdout'].sum())})")

    s_base, g_base = pnl_for_gate(d, "gate_live")
    s_prop, g_prop = pnl_for_gate(d, "gate_proposed")

    # unconditional reference (gate always on, for context only)
    d["_always"] = True
    s_unc, g_unc = pnl_for_gate(d, "_always")

    r_unc = stats_for("UNCONDITIONAL (every day, reference only)", s_unc, g_unc, d, holdout_start)
    r_base = stats_for("BASELINE (live gate: not-GEXq1 AND term-not-inverted)", s_base, g_base, d, holdout_start)
    r_prop = stats_for("PROPOSED (live gate AND COR3M walk-forward bottom quintile)", s_prop, g_prop, d, holdout_start)

    res = pd.DataFrame([r_unc, r_base, r_prop])
    res.to_csv(os.path.join(OUT, "implied_correlation_dispersion_WFCV_results.csv"), index=False)

    daily = d[["date", "vix", "gate_live", "gate_proposed", "cor3m_pctile_wf", "half", "holdout"]].copy()
    daily["pnl_baseline"] = s_base.values
    daily["pnl_proposed"] = s_prop.values
    daily["pnl_unconditional"] = s_unc.values
    daily.to_csv(os.path.join(OUT, "implied_correlation_dispersion_WFCV_daily.csv"), index=False)

    checks = {
        "a_sharpe_improves": r_prop["monthly_sharpe"] > r_base["monthly_sharpe"],
        "b_worst_month_not_worse": abs(r_prop["worst_month_volpts"]) <= abs(r_base["worst_month_volpts"]),
        "c_both_halves_positive": (r_prop["mean_H1"] > 0) and (r_prop["mean_H2"] > 0),
        "d_nw_t_ge_2": r_prop["nw_t"] >= 2.0,
        "e_holdout_positive": r_prop["mean_holdout"] > 0,
    }
    all_pass = all(checks.values())
    verdict = "READY_FOR_JAMES_REVIEW" if all_pass else "done_fail (backtest stage)"

    print("\n" + "=" * 100)
    print(res.to_string(index=False))
    print("=" * 100)
    print(f"\nPASS checks (PROPOSED vs BASELINE): {checks}")
    print(f"VERDICT: {verdict}")

    with open(os.path.join(OUT, "implied_correlation_dispersion_WFCV_summary.md"), "w") as f:
        f.write("# implied_correlation_dispersion: strategy-backtest stage (WFCV)\n\n")
        f.write(f"n={len(d)} days, {d['date'].min().date()} to {d['date'].max().date()} "
                f"(limited by vix_term_history.csv start of 2016-06-23). Holdout = final 20% of sample, "
                f"starting {holdout_start.date()} (n={int(d['holdout'].sum())} days), never used to fit anything "
                f"(the 756d window and 20th-percentile cutoff are fixed constants, not tuned on this data).\n\n")
        f.write("BASELINE reproduces vrp_regime_study.py's gate_both (already live in the vol book's GEX/term gate) "
                "exactly. PROPOSED adds a walk-forward COR3M bottom-quintile filter (trailing 756-day percentile "
                "rank, no lookahead) on top of it. Both charge a conservative 0.05 vol-point cost per gate flip "
                "(regime on/off), as a stand-in for the real iron-condor roll cost.\n\n")
        f.write(res.to_markdown(index=False) + "\n\n")
        f.write("## Pass checks (PROPOSED vs BASELINE)\n\n")
        for k, v in checks.items():
            f.write(f"- {k}: {'PASS' if v else 'FAIL'}\n")
        f.write(f"\n**VERDICT: {verdict}**\n\n")
        if all_pass:
            f.write("All criteria hold. This is a hard stop per protocol: do NOT wire into the vol book. "
                    "Flag loudly to James in #test-env and await his decision.\n")
        else:
            f.write("At least one criterion fails: adding the COR3M walk-forward gate does not reliably improve "
                    "on the vol book's existing GEX/term gate once realistic out-of-sample construction and a "
                    "held-out window are applied. Close this candidate as done_fail at the backtest stage.\n")

    rp = os.path.join(BASE, "r/impliedCorrWFCVPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-800:], file=sys.stderr)
    return 0 if True else 1


if __name__ == "__main__":
    sys.exit(main())
