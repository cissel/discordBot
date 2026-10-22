#!/usr/bin/env python3
"""
multi_asset_trend_study.py  (pre-registered T3, Oct 19 2026)
==============================================================
Question: does applying the live SPY trend rule across a small ETF basket with
vol-scaled, equal-risk weights improve on SPY alone?

Data: daily simple returns 2016-01 onward. SPY/QQQ/GLD from
outputs/markets/cache/<sym>.csv, TLT/IEF from Alpaca daily bars (adjustment=all,
cached in outputs/markets/cache/<sym>_bars.csv, returns from close). Today's
row is dropped as provisional. All assets are inner-joined on the trading
calendar; the price index for each asset is cumprod(1 + r) and the 200dma is
taken on that index. Evaluation window starts on the first date where SPY has a
250-day MA (2016-12-29) so numbers are comparable with study 11
(trend_vol_book_study.py). Cost 0.25bp per unit |exposure change| summed across
assets. All signals use the prior close and are applied to today's return.

Arms
 (1) SPY 200dma x GEX scalar, unit base exposure (LIVE rule; must reproduce
     study 11's Sharpe 0.86 / max DD -15% within rounding).
 (2) SPY 200dma x inverse trailing vol: exposure = min(1.5, 0.12 / trailing
     20-day annualised realised vol) when above the 200dma, else 0. Isolates the
     sizing change from the diversification change.
 (3) Basket TSMOM over SPY, QQQ, GLD, TLT: long when above own 200dma, else
     flat; weight_i = (1/4) x min(1.5, 0.12 / vol20_i); N fixed at 4. Also
     reported with IEF replacing TLT as a robustness line.
 (4) Basket always-long equal-risk (no trend filter), same sizing: the
     diversification-only control.
 (5) Basket (3) with the SPY leg additionally multiplied by the GEX scalar.

Metrics, pooled 2016-12-29 to last complete date and by half (H1 before
2021-07-01, H2 from 2021-07-01): annualised return, Sharpe, max drawdown, mean
gross exposure, fraction of days with any position, trades per year (days with
|exposure change| > 1e-6 per asset, summed). Also the correlation matrix of the
four assets' standalone trend-rule P&L streams and each asset's standalone
Sharpe under 200dma x inverse vol, so the diversification benefit is visible.

PASS RULE (fixed before running)
 T3 PASSES if arm (3) basket TSMOM (TLT version) pooled Sharpe exceeds arm (1)
 live SPY by at least 0.20 AND its max drawdown is smaller in magnitude AND its
 Sharpe is higher in BOTH halves.
 PARTIAL if the pooled Sharpe gain >= 0.20 but one half fails.
 FAIL otherwise.
 Supporting evidence: Newey-West (5 lags) t-stat of the daily P&L difference
 (3) minus (1) with arm (3) rescaled to arm (1)'s full-sample volatility
 (vol-matched), and a paired block bootstrap 95% CI on the Sharpe difference
 (1000 resamples, block length 21, seed 42).

Out: outputs/research/structural/multi_asset_trend_results.csv,
     multi_asset_trend_daily.csv, multi_asset_trend_corr.csv,
     multi_asset_trend_summary.md, multi_asset_trend.png (r/multiAssetTrendPlot.R)
"""
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.expanduser("~/discordBot/python"))
from spyVolRegime import scalar_series  # noqa: E402

CACHE = os.path.expanduser("~/discordBot/outputs/markets/cache")
OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
COST = 0.25e-4
TARGET_VOL = 0.12
LEV_CAP = 1.5
VOL_WIN = 20
MA_LEN = 200
H1_END = pd.Timestamp("2021-07-01")
SEED = 42
N_BOOT = 1000
BLOCK = 21
NW_LAGS = 5
ASSETS = ["SPY", "QQQ", "GLD", "TLT"]


# ----------------------------------------------------------------------------- data
def load_returns():
    out = None
    for s in ("SPY", "QQQ", "GLD"):
        d = pd.read_csv(os.path.join(CACHE, f"{s}.csv"), parse_dates=["date"]).dropna()
        d = d.rename(columns={f"{s}_ret": s})[["date", s]]
        out = d if out is None else out.merge(d, on="date", how="inner")
    for s in ("TLT", "IEF"):
        b = pd.read_csv(os.path.join(CACHE, f"{s}_bars.csv"), parse_dates=["date"]).sort_values("date")
        b[s] = b["c"].pct_change()
        out = out.merge(b[["date", s]].dropna(), on="date", how="inner")
    out = out[out["date"] < pd.Timestamp.today().normalize()].sort_values("date").reset_index(drop=True)
    return out


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


def metrics(pnl, w, dates):
    """pnl: (n,), w: (n, k) exposures per asset, dates: DatetimeIndex."""
    w = np.atleast_2d(w.T).T if w.ndim == 1 else w
    gross = np.abs(w).sum(axis=1)
    chg = np.abs(np.diff(w, axis=0, prepend=w[:1] * 0))
    def block_(m):
        p = pnl[m]; yrs = m.sum() / 252
        return {"ann_ret": p.mean() * 252 * 100, "sharpe": sharpe(p), "max_dd": max_dd(p) * 100,
                "mean_gross": gross[m].mean(), "frac_in": (gross[m] > 1e-9).mean(),
                "trades_per_yr": (chg[m] > 1e-6).sum() / yrs}
    rows = {}
    for per, m in [("pooled", np.ones(len(pnl), bool)), ("H1", np.asarray(dates < H1_END)), ("H2", np.asarray(dates >= H1_END))]:
        rows[per] = {k: round(float(v), 3) for k, v in block_(m).items()}
    return rows


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
    df = load_returns()
    sc = scalar_series(df["date"])
    df = df.merge(sc[["date", "scalar"]], on="date", how="left")
    df["scalar"] = df["scalar"].fillna(1.0)
    dates = pd.DatetimeIndex(df["date"])
    n = len(df)
    syms = ASSETS + ["IEF"]
    R = {s: df[s].values for s in syms}
    IDX = {s: np.cumprod(1 + R[s]) for s in syms}
    MA = {s: pd.Series(IDX[s]).rolling(MA_LEN).mean().values for s in syms}
    ON = {s: trend_state(IDX[s], MA[s]) for s in syms}
    SZ = {s: inv_vol_size(R[s]) for s in syms}
    scal = np.ones(n); scal[1:] = df["scalar"].values[:-1]
    valid = ~np.isnan(pd.Series(IDX["SPY"]).rolling(250).mean().values)
    v0 = dates[valid][0]
    print(f"window {v0.date()} -> {dates[valid][-1].date()}  n={valid.sum()}  (calendar rows {n}, first {dates[0].date()})")

    arms = {}   # name -> (W (n,k), R (n,k), asset list)
    # (1) live SPY: 200dma x GEX scalar, unit base
    W1 = (ON["SPY"] * scal)[:, None]; arms["1 SPY 200dma x GEX scalar (live)"] = (W1, R["SPY"][:, None], ["SPY"])
    # (2) SPY 200dma x inverse vol
    W2 = (ON["SPY"] * SZ["SPY"])[:, None]; arms["2 SPY 200dma x inv vol"] = (W2, R["SPY"][:, None], ["SPY"])
    # (3) basket TSMOM
    def basket(assets, trend=True, spy_scalar=False):
        cols = []
        for s in assets:
            w = SZ[s] / len(assets) * (ON[s] if trend else 1.0)
            if spy_scalar and s == "SPY":
                w = w * scal
            cols.append(w)
        return np.column_stack(cols), np.column_stack([R[s] for s in assets])
    W3, R3 = basket(ASSETS); arms["3 basket TSMOM (SPY QQQ GLD TLT)"] = (W3, R3, ASSETS)
    ief = ["SPY", "QQQ", "GLD", "IEF"]
    W3b, R3b = basket(ief); arms["3b basket TSMOM (IEF for TLT)"] = (W3b, R3b, ief)
    W4, R4 = basket(ASSETS, trend=False); arms["4 basket always-long equal-risk"] = (W4, R4, ASSETS)
    W5, R5 = basket(ASSETS, spy_scalar=True); arms["5 basket TSMOM, SPY leg x GEX scalar"] = (W5, R5, ASSETS)

    rows, daily = [], {"date": dates[valid]}
    pnls = {}
    for name, (W, Rm, assets) in arms.items():
        p = pnl_from_weights(W, Rm)
        pnls[name] = p[valid]; daily[name] = p[valid]
        for per, m in metrics(p[valid], W[valid], dates[valid]).items():
            rows.append({"arm": name, "period": per, **m})
    res = pd.DataFrame(rows)

    # per-asset standalone 200dma x inv vol (unit weight) and correlation of P&L streams
    standalone = {}
    for s in syms:
        W = (ON[s] * SZ[s])[:, None]
        p = pnl_from_weights(W, R[s][:, None])[valid]
        standalone[s] = p
        for per, m in metrics(p, W[valid], dates[valid]).items():
            rows.append({"arm": f"standalone {s} 200dma x inv vol", "period": per, **m})
    res = pd.DataFrame(rows)
    corr = pd.DataFrame({s: standalone[s] for s in ASSETS}).corr().round(3)
    corr_ief = pd.DataFrame({s: standalone[s] for s in ief}).corr().round(3)

    # pass rule
    g = lambda a, per: res[(res.arm == a) & (res.period == per)].iloc[0]
    a1, a3 = "1 SPY 200dma x GEX scalar (live)", "3 basket TSMOM (SPY QQQ GLD TLT)"
    gain = g(a3, "pooled").sharpe - g(a1, "pooled").sharpe
    dd_ok = abs(g(a3, "pooled").max_dd) < abs(g(a1, "pooled").max_dd)
    h1_ok = g(a3, "H1").sharpe > g(a1, "H1").sharpe
    h2_ok = g(a3, "H2").sharpe > g(a1, "H2").sharpe
    if gain >= 0.20 and dd_ok and h1_ok and h2_ok:
        verdict = "PASS"
    elif gain >= 0.20 and (h1_ok or h2_ok):
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
    p1, p3 = pnls[a1], pnls[a3]
    p3_vm = p3 * (p1.std(ddof=1) / p3.std(ddof=1))
    t_nw = nw_t(p3_vm - p1)
    lo, hi = block_bootstrap_sharpe_diff(p3, p1)
    # same for the IEF line and arm 2, 4, 5 for context
    extras = {}
    for a in ["2 SPY 200dma x inv vol", "3b basket TSMOM (IEF for TLT)", "4 basket always-long equal-risk", "5 basket TSMOM, SPY leg x GEX scalar"]:
        pa = pnls[a]; extras[a] = (nw_t(pa * (p1.std(ddof=1) / pa.std(ddof=1)) - p1), *block_bootstrap_sharpe_diff(pa, p1))

    # reproduction check against study 11
    ref = pd.read_csv(os.path.join(OUT, "trend_vol_book_results.csv"))
    ref = ref[(ref.test == "T1") & (ref.arm == "200dma x scalar")].iloc[0]
    repro = (abs(g(a1, "pooled").sharpe - ref.sharpe) < 0.01) and (abs(g(a1, "pooled").max_dd - ref.max_dd) < 0.1)

    # write
    res.to_csv(os.path.join(OUT, "multi_asset_trend_results.csv"), index=False)
    pd.DataFrame(daily).to_csv(os.path.join(OUT, "multi_asset_trend_daily.csv"), index=False)
    corr.to_csv(os.path.join(OUT, "multi_asset_trend_corr.csv"))
    pd.set_option("display.width", 240)
    print(res.to_string(index=False))
    print("\ncorrelation of standalone trend-rule P&L streams\n", corr.to_string())
    print("\n(with IEF)\n", corr_ief.to_string())
    print(f"\nreproduction of study 11 (200dma x scalar: Sharpe {ref.sharpe}, DD {ref.max_dd}): {'OK' if repro else 'MISMATCH'}")
    print(f"Sharpe gain (3)-(1) = {gain:+.3f}; DD smaller: {dd_ok}; H1 higher: {h1_ok}; H2 higher: {h2_ok}")
    print(f"NW({NW_LAGS}) t of vol-matched daily diff (3)-(1): {t_nw:+.2f}; bootstrap 95% CI on Sharpe diff: [{lo:+.3f}, {hi:+.3f}]")
    for a, (t, l, h) in extras.items():
        print(f"  vs (1): {a}: NW t {t:+.2f}, Sharpe diff CI [{l:+.3f}, {h:+.3f}]")
    print(f"\nVERDICT T3: {verdict}")

    with open(os.path.join(OUT, "multi_asset_trend_summary.md"), "w") as f:
        f.write("T3 multi-asset trend study (pre-registered)\n\n")
        f.write(f"Verdict: {verdict}\n\n")
        f.write("Rule: PASS if basket TSMOM (arm 3) pooled Sharpe beats live SPY (arm 1) by >= 0.20 AND smaller max DD AND higher Sharpe in both halves; "
                "PARTIAL if pooled gain >= 0.20 but one half fails; FAIL otherwise.\n\n")
        f.write(f"Window {v0.date()} to {dates[valid][-1].date()}, n = {int(valid.sum())} days. Cost 0.25bp per unit |exposure change| summed across assets.\n")
        f.write(f"Study 11 reproduction of arm 1: {'OK' if repro else 'MISMATCH'} (ref Sharpe {ref.sharpe}, DD {ref.max_dd}).\n\n")
        f.write(f"Sharpe gain (3)-(1): {gain:+.3f}. DD smaller: {dd_ok}. H1 higher: {h1_ok}. H2 higher: {h2_ok}.\n")
        f.write(f"NW({NW_LAGS}) t of vol-matched daily P&L difference (3)-(1): {t_nw:+.2f}. "
                f"Paired block bootstrap ({N_BOOT} x block {BLOCK}) 95% CI on Sharpe difference: [{lo:+.3f}, {hi:+.3f}].\n\n")
        for a, (t, l, h) in extras.items():
            f.write(f"vs arm 1, {a}: NW t {t:+.2f}, Sharpe diff CI [{l:+.3f}, {h:+.3f}]\n")
        f.write("\n" + res.to_markdown(index=False) + "\n\n")
        f.write("Correlation of standalone 200dma x inv vol P&L streams\n\n" + corr.to_markdown() + "\n\n")
        f.write("With IEF in place of TLT\n\n" + corr_ief.to_markdown() + "\n")

    rp = os.path.expanduser("~/discordBot/r/multiAssetTrendPlot.R")
    if os.path.exists(rp):
        rr = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if rr.returncode:
            print(rr.stderr[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
