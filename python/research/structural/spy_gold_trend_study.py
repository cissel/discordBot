#!/usr/bin/env python3
"""
spy_gold_trend_study.py  (pre-registered, Oct 20 2026)
======================================================
Question: does a two-asset SPY + gold trend book beat SPY alone, on a holdout
the idea was NOT derived from?

Background. Study T3 (multi_asset_trend_study.py, 2016-12-29 to 2026-10) found
gold was the only real diversifier in a SPY/QQQ/GLD/TLT basket (trend P&L corr
0.04 with SPY, standalone Sharpe 0.80). That is a post-hoc observation on the
T3 window, so this study tests SPY + gold first on a DISJOINT holdout
(2011-05-02 to 2016-12-28, first ~250 rows burned as warmup, so the evaluated
holdout starts in 2012) and then confirms on the T3 window (2016-12-29 to the
last complete date).

Data. SPY: SPX price from outputs/markets/cache/spy_gex_daily.csv (column
price) for dates before SPY.csv starts (2016-01-05), SPY.csv SPY_ret after.
Gold: GLD.csv GLD_ret for 2016-01-05 onward; before that, GLD adjusted close
from the Yahoo chart API saved to outputs/markets/cache/GLD_long_history.csv
(Stooq CSV was blocked by a JS wall and 'Access denied' on download, FRED timed
out from this host). Overlap check 2016-2026 of the long-history source vs
GLD.csv returns: corr 0.9957 (n 2713); Alpaca SIP bars vs GLD.csv 0.9999 but
SIP only starts 2016. Calendars inner-joined. Today's row dropped as
provisional.

Conventions (identical to T3). Price index = cumprod(1 + r), 200dma on that
index, trend on[t] = index[t-1] > ma[t-1]. Size_i = min(1.5, 0.12 / trailing
20-day annualised realised vol_i), from returns through t-1. Cost 0.25bp per
unit |exposure change| per asset. N = 2 fixed for arm 3.

Arms
 (1) SPY 200dma x inverse vol, unit budget (N = 1).
 (2) gold 200dma x inverse vol, unit budget (N = 1, reference only).
 (3) SPY + gold 50/50 vol-sized trend, N = 2 fixed: weight_i = 0.5 x on_i x
     size_i. When one asset is out of trend its half is parked in cash.
 (4) SPY + gold 50/50 always-long vol-sized (no trend filter): the
     diversification-only control.
 (5) SPY + gold trend, re-normalised: weight_i = on_i x size_i / n_on, so when
     one asset is out of trend the other receives the full budget (redeploy).

Windows. HOLDOUT: first date with a 250-day SPX MA (warmup as in T3) to
2016-12-28. CONFIRM: 2016-12-29 to last complete date. Halves: holdout split at
the median evaluated trading day; confirm split at 2021-07-01 to match T3.

Metrics per arm per window (and halves): annualised return (%), Sharpe, max
drawdown (%), mean gross exposure, fraction of days with any position, trades
per year (days with |exposure change| > 1e-6 per asset, summed). Correlation of
the two legs' standalone trend P&L (arms 1 and 2) within each window and half.
NW(5) t of the vol-matched daily P&L difference (3) minus (1), arm 3 rescaled
to arm 1's window volatility. Paired circular block bootstrap (1000 resamples,
block 21, seed 42) 95% CI on the Sharpe difference (3) minus (1). The same
statistics are reported for (5) minus (3), (5) minus (1) and (4) minus (1).

PRE-REGISTERED PASS RULE (fixed before running)
 PASS if, on the HOLDOUT, arm 3 Sharpe exceeds arm 1 Sharpe by at least 0.20
   AND arm 3 max drawdown is smaller in magnitude than arm 1
   AND the bootstrap 95% CI lower bound on the holdout Sharpe difference
       (3) minus (1) is above -0.05
   AND, on the CONFIRM window, arm 3 Sharpe is higher than arm 1 Sharpe.
 PARTIAL if the holdout conditions (Sharpe gain >= 0.20 and smaller DD) hold
   but the confirm Sharpe is not higher, or the holdout CI lower bound is at or
   below -0.05.
 FAIL otherwise.
 Separately reported, no verdict attached: does arm 5 (redeploy) beat arm 3
 (park) on Sharpe and max DD in each window, with NW t and bootstrap CI, so the
 implementation choice is evidence-based.

Out: outputs/research/structural/spy_gold_trend_results.csv,
     spy_gold_trend_daily.csv, spy_gold_trend_summary.md,
     spy_gold_trend.png (r/spyGoldTrendPlot.R)
"""
import os
import subprocess
import sys

import numpy as np
import pandas as pd

CACHE = os.path.expanduser("~/discordBot/outputs/markets/cache")
OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
COST = 0.25e-4
TARGET_VOL = 0.12
LEV_CAP = 1.5
VOL_WIN = 20
MA_LEN = 200
WARMUP = 250
SPLIT = pd.Timestamp("2016-12-29")       # first day of CONFIRM (T3 window start)
CONFIRM_H1_END = pd.Timestamp("2021-07-01")
SEED = 42
N_BOOT = 1000
BLOCK = 21
NW_LAGS = 5
ARMS = {
    "1": "1 SPY 200dma x inv vol",
    "2": "2 gold 200dma x inv vol",
    "3": "3 SPY+gold trend N=2 (park)",
    "4": "4 SPY+gold always-long vol-sized",
    "5": "5 SPY+gold trend renormalised (redeploy)",
}


# ----------------------------------------------------------------------------- data
def load_returns():
    spx = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"])[["date", "price"]].dropna().sort_values("date")
    spx["SPY"] = spx["price"].pct_change()
    spy = pd.read_csv(os.path.join(CACHE, "SPY.csv"), parse_dates=["date"]).dropna().rename(columns={"SPY_ret": "SPY"})
    gld = pd.read_csv(os.path.join(CACHE, "GLD.csv"), parse_dates=["date"]).dropna().rename(columns={"GLD_ret": "GLD"})
    gl = pd.read_csv(os.path.join(CACHE, "GLD_long_history.csv"), parse_dates=["date"])[["date", "GLD_ret"]].dropna().rename(columns={"GLD_ret": "GLD"})
    s0 = spy["date"].min()
    s = pd.concat([spx[spx["date"] < s0][["date", "SPY"]].dropna(), spy[["date", "SPY"]]]).sort_values("date")
    g = pd.concat([gl[gl["date"] < s0], gld[["date", "GLD"]]]).sort_values("date")
    df = s.merge(g, on="date", how="inner")
    df = df[df["date"] < pd.Timestamp.today().normalize()].sort_values("date").reset_index(drop=True)
    return df, s0


# ----------------------------------------------------------------------------- stats
def max_dd(pnl):
    eq = np.cumprod(1 + pnl)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def sharpe(pnl):
    s = pnl.std(ddof=1)
    return float(pnl.mean() / s * np.sqrt(252)) if s > 0 else np.nan


def nw_t(x, lags=NW_LAGS):
    x = np.asarray(x, float)
    n = len(x); m = x.mean(); u = x - m
    s = (u @ u) / n
    for L in range(1, lags + 1):
        s += 2 * (1 - L / (lags + 1)) * (u[L:] @ u[:-L]) / n
    return float(m / np.sqrt(s / n))


def block_bootstrap_sharpe_diff(a, b, n_boot=N_BOOT, block=BLOCK, seed=SEED):
    """Paired circular block bootstrap of Sharpe(a) - Sharpe(b)."""
    rng = np.random.default_rng(seed)
    n = len(a); nb = int(np.ceil(n / block)); diffs = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        diffs[i] = sharpe(a[idx]) - sharpe(b[idx])
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def compare(pa, pb):
    """NW t of vol-matched daily diff (a - b, a rescaled to b's vol) and bootstrap CI on Sharpe diff."""
    pa_vm = pa * (pb.std(ddof=1) / pa.std(ddof=1))
    lo, hi = block_bootstrap_sharpe_diff(pa, pb)
    return {"sharpe_diff": sharpe(pa) - sharpe(pb), "nw_t": nw_t(pa_vm - pb), "ci_lo": lo, "ci_hi": hi}


def block_metrics(pnl, W, m):
    gross = np.abs(W).sum(axis=1)
    chg = np.abs(np.diff(W, axis=0, prepend=W[:1] * 0))
    p = pnl[m]; yrs = m.sum() / 252
    return {"ann_ret": p.mean() * 252 * 100, "sharpe": sharpe(p), "max_dd": max_dd(p) * 100,
            "mean_gross": gross[m].mean(), "frac_in": (gross[m] > 1e-9).mean(),
            "trades_per_yr": (chg[m] > 1e-6).sum() / yrs, "n_days": int(m.sum())}


# ----------------------------------------------------------------------------- rules
def trend_state(idx, ma):
    """on[t] = 1 if idx[t-1] > ma[t-1] (prior close), 0 otherwise; NaN MA -> 0."""
    n = len(idx); on = np.zeros(n)
    on[1:] = np.where(np.isnan(ma[:-1]), 0.0, (idx[:-1] > ma[:-1]).astype(float))
    return on


def inv_vol_size(r):
    """min(1.5, 0.12 / vol20) using returns through t-1."""
    vol = pd.Series(r).rolling(VOL_WIN).std(ddof=1).values * np.sqrt(252)
    size = np.minimum(LEV_CAP, TARGET_VOL / vol)
    size = np.where(np.isnan(size), 0.0, size)
    out = np.zeros(len(r)); out[1:] = size[:-1]
    return out


def pnl_from_weights(W, R):
    """W: (n, k) weights set from t-1 info, R: (n, k) returns. Cost on |dW| summed across assets."""
    cost = np.abs(np.diff(W, axis=0, prepend=W[:1] * 0)).sum(axis=1) * COST
    return (W * R).sum(axis=1) - cost


# ----------------------------------------------------------------------------- main
def main():
    np.random.seed(SEED)
    df, splice = load_returns()
    dates = pd.DatetimeIndex(df["date"])
    n = len(df)
    syms = ["SPY", "GLD"]
    R = {s: df[s].values for s in syms}
    IDX = {s: np.cumprod(1 + R[s]) for s in syms}
    MA = {s: pd.Series(IDX[s]).rolling(MA_LEN).mean().values for s in syms}
    ON = {s: trend_state(IDX[s], MA[s]) for s in syms}
    SZ = {s: inv_vol_size(R[s]) for s in syms}
    valid = ~np.isnan(pd.Series(IDX["SPY"]).rolling(WARMUP).mean().values)
    Rm = np.column_stack([R[s] for s in syms])

    W = {}
    W["1"] = np.column_stack([ON["SPY"] * SZ["SPY"], np.zeros(n)])
    W["2"] = np.column_stack([np.zeros(n), ON["GLD"] * SZ["GLD"]])
    W["3"] = np.column_stack([0.5 * ON[s] * SZ[s] for s in syms])
    W["4"] = np.column_stack([0.5 * SZ[s] for s in syms])
    n_on = ON["SPY"] + ON["GLD"]
    W["5"] = np.column_stack([np.where(n_on > 0, ON[s] * SZ[s] / np.maximum(n_on, 1), 0.0) for s in syms])
    P = {k: pnl_from_weights(W[k], Rm) for k in W}

    windows = {"HOLDOUT": valid & np.asarray(dates < SPLIT), "CONFIRM": valid & np.asarray(dates >= SPLIT)}
    half_end = {}
    hd = dates[windows["HOLDOUT"]]; half_end["HOLDOUT"] = hd[len(hd) // 2]
    half_end["CONFIRM"] = CONFIRM_H1_END
    for w, m in windows.items():
        print(f"{w}: {dates[m][0].date()} -> {dates[m][-1].date()}  n={m.sum()}  H1 ends before {half_end[w].date()}")
    print(f"calendar rows {n}, first {dates[0].date()}, SPY/GLD splice from SPX/Yahoo to SPY.csv/GLD.csv at {splice.date()}")

    rows, cmp_rows = [], []
    for w, m in windows.items():
        periods = {"full": m, "H1": m & np.asarray(dates < half_end[w]), "H2": m & np.asarray(dates >= half_end[w])}
        for k, name in ARMS.items():
            for per, mm in periods.items():
                rows.append({"window": w, "arm": name, "period": per, **{a: round(float(b), 4) for a, b in block_metrics(P[k], W[k], mm).items()}})
        for per, mm in periods.items():
            rows.append({"window": w, "arm": "leg corr (1 vs 2 trend P&L)", "period": per,
                         "ann_ret": np.nan, "sharpe": round(float(np.corrcoef(P["1"][mm], P["2"][mm])[0, 1]), 4),
                         "max_dd": np.nan, "mean_gross": np.nan, "frac_in": np.nan, "trades_per_yr": np.nan, "n_days": int(mm.sum())})
        for a, b in [("3", "1"), ("5", "3"), ("5", "1"), ("4", "1"), ("2", "1")]:
            c = compare(P[a][m], P[b][m])
            cmp_rows.append({"window": w, "comparison": f"({a}) minus ({b})", **{x: round(float(y), 4) for x, y in c.items()}})
    res = pd.DataFrame(rows)
    cmp = pd.DataFrame(cmp_rows)

    # pass rule
    g = lambda w, k, per="full": res[(res.window == w) & (res.arm == ARMS[k]) & (res.period == per)].iloc[0]
    c = lambda w, s: cmp[(cmp.window == w) & (cmp.comparison == s)].iloc[0]
    gain_h = g("HOLDOUT", "3").sharpe - g("HOLDOUT", "1").sharpe
    dd_h = abs(g("HOLDOUT", "3").max_dd) < abs(g("HOLDOUT", "1").max_dd)
    ci_h = c("HOLDOUT", "(3) minus (1)").ci_lo > -0.05
    conf_ok = g("CONFIRM", "3").sharpe > g("CONFIRM", "1").sharpe
    holdout_core = gain_h >= 0.20 and dd_h
    if holdout_core and ci_h and conf_ok:
        verdict = "PASS"
    elif holdout_core and (not conf_ok or not ci_h):
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
    redeploy = {}
    for w in windows:
        redeploy[w] = {"sharpe_5": g(w, "5").sharpe, "sharpe_3": g(w, "3").sharpe,
                       "dd_5": g(w, "5").max_dd, "dd_3": g(w, "3").max_dd,
                       "nw_t": c(w, "(5) minus (3)").nw_t, "ci_lo": c(w, "(5) minus (3)").ci_lo, "ci_hi": c(w, "(5) minus (3)").ci_hi}

    # reproduction check: arm 1 on CONFIRM should match T3 arm 2 (same returns, same rule)
    repro_msg = "T3 results file not found"
    t3p = os.path.join(OUT, "multi_asset_trend_results.csv")
    if os.path.exists(t3p):
        t3 = pd.read_csv(t3p)
        ref = t3[(t3.arm == "2 SPY 200dma x inv vol") & (t3.period == "pooled")]
        if len(ref):
            ref = ref.iloc[0]
            ok = abs(g("CONFIRM", "1").sharpe - ref.sharpe) < 0.02 and abs(g("CONFIRM", "1").max_dd - ref.max_dd) < 0.3
            repro_msg = f"{'OK' if ok else 'MISMATCH'} (this {g('CONFIRM', '1').sharpe:.3f} / {g('CONFIRM', '1').max_dd:.2f} vs T3 arm 2 {ref.sharpe} / {ref.max_dd})"

    # daily output
    ev = valid
    daily = pd.DataFrame({"date": dates[ev], "window": np.where(dates[ev] < SPLIT, "HOLDOUT", "CONFIRM")})
    for k, name in ARMS.items():
        daily[name] = P[k][ev]
        daily[f"gross_{k}"] = np.abs(W[k][ev]).sum(axis=1)
    daily["on_SPY"] = ON["SPY"][ev]; daily["on_GLD"] = ON["GLD"][ev]
    daily["roll252_leg_corr"] = pd.Series(P["1"][ev]).rolling(252).corr(pd.Series(P["2"][ev])).values

    res.to_csv(os.path.join(OUT, "spy_gold_trend_results.csv"), index=False)
    cmp.to_csv(os.path.join(OUT, "spy_gold_trend_comparisons.csv"), index=False)
    daily.to_csv(os.path.join(OUT, "spy_gold_trend_daily.csv"), index=False)

    pd.set_option("display.width", 240)
    print(res.to_string(index=False))
    print("\n", cmp.to_string(index=False))
    print(f"\nreproduction of T3 arm 2 on CONFIRM: {repro_msg}")
    print(f"HOLDOUT Sharpe gain (3)-(1) = {gain_h:+.3f} (need >= 0.20); DD smaller: {dd_h}; CI lower bound > -0.05: {ci_h}; CONFIRM Sharpe higher: {conf_ok}")
    for w, r in redeploy.items():
        print(f"{w} redeploy vs park: Sharpe {r['sharpe_5']:.3f} vs {r['sharpe_3']:.3f}, DD {r['dd_5']:.2f} vs {r['dd_3']:.2f}, NW t {r['nw_t']:+.2f}, CI [{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]")
    print(f"\nVERDICT: {verdict}")

    with open(os.path.join(OUT, "spy_gold_trend_summary.md"), "w") as f:
        f.write("SPY + gold trend study (pre-registered holdout test of the T3 gold observation)\n\n")
        f.write(f"Verdict: {verdict}\n\n")
        f.write("Rule: PASS if on the HOLDOUT arm 3 (SPY+gold trend, N=2 park) beats arm 1 (SPY trend x inv vol) on Sharpe by >= 0.20 AND has a smaller max DD "
                "AND the block-bootstrap 95% CI lower bound on the holdout Sharpe difference is above -0.05, AND arm 3 Sharpe is higher on the CONFIRM window. "
                "PARTIAL if the holdout Sharpe and DD conditions hold but confirm fails or the CI straddles. FAIL otherwise.\n\n")
        for w, m in windows.items():
            f.write(f"{w}: {dates[m][0].date()} to {dates[m][-1].date()}, n = {int(m.sum())} days, H1/H2 split at {half_end[w].date()}.\n")
        f.write(f"Cost 0.25bp per unit |exposure change| per asset. Signals from the prior close. Gold before {splice.date()} from Yahoo chart API "
                "(GLD_long_history.csv, overlap corr with GLD.csv 0.9957), SPY before that date from SPX price in spy_gex_daily.csv.\n")
        f.write(f"Reproduction of T3 arm 2 on CONFIRM: {repro_msg}.\n\n")
        f.write(f"HOLDOUT Sharpe gain (3)-(1): {gain_h:+.3f}. DD smaller: {dd_h}. CI lower bound > -0.05: {ci_h}. CONFIRM Sharpe higher: {conf_ok}.\n\n")
        f.write("Park vs redeploy (arm 5 minus arm 3):\n")
        for w, r in redeploy.items():
            f.write(f"  {w}: Sharpe {r['sharpe_5']:.3f} vs {r['sharpe_3']:.3f}, max DD {r['dd_5']:.2f} vs {r['dd_3']:.2f}, NW t {r['nw_t']:+.2f}, Sharpe diff CI [{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]\n")
        f.write("\nComparisons (first arm vol-matched to second for the NW t; bootstrap 1000 x block 21, seed 42)\n\n" + cmp.to_markdown(index=False) + "\n\n")
        f.write(res.to_markdown(index=False) + "\n")

    rp = os.path.expanduser("~/discordBot/r/spyGoldTrendPlot.R")
    if os.path.exists(rp):
        rr = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if rr.returncode:
            print(rr.stderr[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
