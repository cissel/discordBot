#!/usr/bin/env python3
"""
Pre-registered dealer-positioning study: SqueezeMetrics GEX and DIX vs SPX, 2011-2026.

Tests (all signals use trailing-252-day ranks, forward returns use closes strictly after t):
  A. DIX decile vs forward 5d / 21d SPX return (monotonicity, top-minus-bottom spread, Spearman)
  B. GEX sign and GEX quintile vs next-day |ret|, next-5d realized vol, RV5 / VIX ratio
  C. Interaction: low GEX & high DIX vs high GEX & low DIX, forward 5d / 21d return
  D. VIX term structure inversion (vix9d > vix3m) vs next 5d / 21d return and realized vol (2016+)
Then one strategy from the single strongest return-spread finding, 1bp per side.

Inference:
  - Spread t-stats use Newey-West HAC with lag = horizon - 1 (overlapping forward returns).
  - Bootstrap 90% CIs: 10,000 draws, circular block bootstrap with block length = horizon
    (block length 1 for 1-day quantities), resampling each bucket independently.
  - Both time halves reported, with an explicit sign / magnitude agreement flag.
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy import stats

ROOT = "/home/jhcv/discordBot"
CACHE = os.path.join(ROOT, "outputs/markets/cache")
OUT = os.path.join(ROOT, "outputs/research/structural")
os.makedirs(OUT, exist_ok=True)

RNG = np.random.default_rng(20261019)
NBOOT = 10000
WIN = 252
COST = 1e-4  # 1bp per side

# ------------------------------------------------------------------ data
g = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"]).sort_values("date")
v = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"])
v["vix"] = pd.to_numeric(v["VIXCLS_val"], errors="coerce")
v = v.dropna(subset=["vix"])[["date", "vix"]]
t = pd.read_csv(os.path.join(CACHE, "vix_term_history.csv"), parse_dates=["date"])

df = g.merge(v, on="date", how="left").merge(t, on="date", how="left").reset_index(drop=True)
df["vix"] = df["vix"].ffill(limit=3)
p = df["price"].values
lp = np.log(p)
n = len(df)

# forward log returns: fwd_h[t] = log(p[t+h]) - log(p[t]) -> uses closes strictly after t
for h in (1, 5, 21):
    f = np.full(n, np.nan)
    f[:-h] = lp[h:] - lp[:-h]
    df[f"fwd{h}"] = f
df["r1"] = df["fwd1"]  # daily log return from t to t+1

# next-day absolute return and next-5-day realized vol (returns t->t+1 ... t+4->t+5)
df["abs1"] = df["fwd1"].abs()
r1 = df["r1"].values
rv5 = np.full(n, np.nan)
for i in range(n - 5):
    w = r1[i:i + 5]
    rv5[i] = np.sqrt(np.mean(w ** 2) * 252)
df["rv5"] = rv5
df["impl"] = df["vix"] / 100.0           # annualized implied vol
df["ratio5"] = df["rv5"] / df["impl"]    # realized / implied, both annualized (= RV5*sqrt(5/252) / (VIX/100*sqrt(5/252)))
df["excess_var"] = df["rv5"] ** 2 - df["impl"] ** 2  # realized minus implied variance (annualized)


def trailing_pct(x, win=WIN):
    """Percentile rank of x[t] within x[t-win+1 .. t] (inclusive of t). No future data."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(win - 1, len(x)):
        w = x[i - win + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = (w < w[-1]).sum() / (win - 1)
    return out


df["dix_pct"] = trailing_pct(df["dix"].values)
df["gex_pct"] = trailing_pct(df["gex"].values)
df["dix_dec"] = np.floor(df["dix_pct"] * 10).clip(upper=9)
df["gex_q"] = np.floor(df["gex_pct"] * 5).clip(upper=4)
df["gex_neg"] = (df["gex"] < 0).astype(float)
df["inv"] = np.where(df["vix9d"].notna() & df["vix3m"].notna(), (df["vix9d"] > df["vix3m"]).astype(float), np.nan)

half_cut = pd.Timestamp("2019-01-01")
df["half"] = np.where(df["date"] < half_cut, "H1_2011_2018", "H2_2019_2026")
df["half_d"] = np.where(df["date"] < pd.Timestamp("2021-01-01"), "H1_2016_2020", "H2_2021_2026")


# ------------------------------------------------------------------ inference helpers
def nw_mean_diff(y1, y0, lag):
    """Newey-West t-stat for mean(y1) - mean(y0) via regression on a dummy, lag = lag."""
    y1 = np.asarray(y1, float); y0 = np.asarray(y0, float)
    y = np.concatenate([y1, y0])
    X = np.column_stack([np.ones(len(y)), np.concatenate([np.ones(len(y1)), np.zeros(len(y0))])])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        G = Xe[l:].T @ Xe[:-l]
        S += w * (G + G.T)
    XtX_inv = np.linalg.inv(X.T @ X)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(V[1, 1])
    return beta[1], beta[1] / se if se > 0 else np.nan


def block_boot_means(y, blk, nb=NBOOT):
    """Circular block bootstrap of the mean of y (y ordered in time)."""
    y = np.asarray(y, float)
    m = len(y)
    if m == 0:
        return np.full(nb, np.nan)
    blk = max(1, min(blk, m))
    nblk = int(np.ceil(m / blk))
    starts = RNG.integers(0, m, size=(nb, nblk))
    idx = (starts[:, :, None] + np.arange(blk)[None, None, :]) % m
    idx = idx.reshape(nb, -1)[:, :m]
    return y[idx].mean(axis=1)


def spread_ci(y1, y0, h):
    b = block_boot_means(y1, h) - block_boot_means(y0, h)
    return np.nanpercentile(b, 5), np.nanpercentile(b, 95)


rows = []
HYP = []


def add_row(**kw):
    rows.append(kw)


def record_hyp(name):
    HYP.append(name)


def bucket_table(sub, col, target, h, test, period):
    for b, grp in sub.groupby(col):
        y = grp[target].dropna().values
        add_row(test=test, period=period, target=target, bucket=f"{col}={int(b)}", n=len(y),
                mean=y.mean() if len(y) else np.nan, median=np.median(y) if len(y) else np.nan,
                spread=np.nan, ci_lo=np.nan, ci_hi=np.nan, t_nw=np.nan, spearman=np.nan, p_spear=np.nan)


def spread_row(sub, hi_mask, lo_mask, target, h, test, period, label, spear_col=None):
    y1 = sub.loc[hi_mask, target].dropna().values
    y0 = sub.loc[lo_mask, target].dropna().values
    if len(y1) < 10 or len(y0) < 10:
        add_row(test=test, period=period, target=target, bucket=label, n=len(y1) + len(y0),
                mean=np.nan, median=np.nan, spread=np.nan, ci_lo=np.nan, ci_hi=np.nan, t_nw=np.nan,
                spearman=np.nan, p_spear=np.nan, n_hi=len(y1), n_lo=len(y0))
        return np.nan, np.nan
    d, tt = nw_mean_diff(y1, y0, max(h - 1, 0))
    lo, hi = spread_ci(y1, y0, h)
    sp, ps = (np.nan, np.nan)
    if spear_col is not None:
        s2 = sub[[spear_col, target]].dropna()
        sp, ps = stats.spearmanr(s2[spear_col], s2[target])
    add_row(test=test, period=period, target=target, bucket=label, n=len(y1) + len(y0),
            mean=np.nan, median=np.nan, spread=d, ci_lo=lo, ci_hi=hi, t_nw=tt, spearman=sp, p_spear=ps,
            n_hi=len(y1), n_lo=len(y0))
    return d, tt


periods = [("FULL", df), ("H1_2011_2018", df[df.half == "H1_2011_2018"]), ("H2_2019_2026", df[df.half == "H2_2019_2026"])]

# ------------------------------------------------------------------ A. DIX deciles
spreadsA = {}
for h in (5, 21):
    tgt = f"fwd{h}"
    for pname, sub in periods:
        s = sub.dropna(subset=["dix_dec", tgt])
        bucket_table(s, "dix_dec", tgt, h, "A_DIX_decile", pname)
        d, tt = spread_row(s, s.dix_dec == 9, s.dix_dec == 0, tgt, h, "A_DIX_decile", pname,
                           "top_minus_bottom_decile", spear_col="dix_dec")
        spreadsA[(h, pname)] = (d, tt)
    record_hyp(f"A: DIX top-bottom decile spread fwd{h}")
    record_hyp(f"A: Spearman DIX decile vs fwd{h}")

# ------------------------------------------------------------------ B. GEX vol regime
spreadsB = {}
for tgt, h in (("abs1", 1), ("rv5", 5), ("ratio5", 5), ("excess_var", 5)):
    for pname, sub in periods:
        s = sub.dropna(subset=["gex_q", tgt, "vix"])
        bucket_table(s, "gex_neg", tgt, h, "B_GEX_sign", pname)
        bucket_table(s, "gex_q", tgt, h, "B_GEX_quintile", pname)
        d, tt = spread_row(s, s.gex_neg == 1, s.gex_neg == 0, tgt, h, "B_GEX_sign", pname, "neg_minus_pos")
        spreadsB[("sign", tgt, pname)] = (d, tt)
        d, tt = spread_row(s, s.gex_q == 0, s.gex_q == 4, tgt, h, "B_GEX_quintile", pname, "q0_minus_q4", spear_col="gex_q")
        spreadsB[("q", tgt, pname)] = (d, tt)
        # share of days where realized > implied
        if tgt == "ratio5":
            for lab, m in (("neg", s.gex_neg == 1), ("pos", s.gex_neg == 0), ("q0", s.gex_q == 0), ("q4", s.gex_q == 4)):
                yy = s.loc[m, "ratio5"]
                add_row(test="B_share_RV_gt_IV", period=pname, target="P(ratio5>1)", bucket=lab, n=len(yy),
                        mean=(yy > 1).mean(), median=np.nan, spread=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                        t_nw=np.nan, spearman=np.nan, p_spear=np.nan)
    record_hyp(f"B: GEX sign neg-pos {tgt}")
    record_hyp(f"B: GEX q0-q4 {tgt}")

# ------------------------------------------------------------------ C. Interaction
spreadsC = {}
for h in (5, 21):
    tgt = f"fwd{h}"
    for pname, sub in periods:
        s = sub.dropna(subset=["gex_q", "dix_dec", tgt])
        lowG_highD = (s.gex_q == 0) & (s.dix_dec >= 8)
        highG_lowD = (s.gex_q == 4) & (s.dix_dec <= 1)
        for lab, m in (("lowGEX_highDIX", lowG_highD), ("highGEX_lowDIX", highG_lowD)):
            y = s.loc[m, tgt].values
            add_row(test="C_interaction", period=pname, target=tgt, bucket=lab, n=len(y),
                    mean=y.mean() if len(y) else np.nan, median=np.median(y) if len(y) else np.nan,
                    spread=np.nan, ci_lo=np.nan, ci_hi=np.nan, t_nw=np.nan, spearman=np.nan, p_spear=np.nan)
        d, tt = spread_row(s, lowG_highD, highG_lowD, tgt, h, "C_interaction", pname, "lowGhighD_minus_highGlowD")
        spreadsC[(h, pname)] = (d, tt)
    record_hyp(f"C: interaction spread fwd{h}")

# ------------------------------------------------------------------ D. Term structure inversion
dsub = df.dropna(subset=["inv"])
periodsD = [("FULL_2016_2026", dsub), ("H1_2016_2020", dsub[dsub.half_d == "H1_2016_2020"]), ("H2_2021_2026", dsub[dsub.half_d == "H2_2021_2026"])]
spreadsD = {}
for tgt, h in (("fwd5", 5), ("fwd21", 21), ("rv5", 5), ("ratio5", 5)):
    for pname, sub in periodsD:
        s = sub.dropna(subset=[tgt])
        bucket_table(s, "inv", tgt, h, "D_term_inversion", pname)
        d, tt = spread_row(s, s.inv == 1, s.inv == 0, tgt, h, "D_term_inversion", pname, "inverted_minus_normal")
        spreadsD[(tgt, pname)] = (d, tt)
    record_hyp(f"D: inversion spread {tgt}")

# ------------------------------------------------------------------ half agreement flags
agree = []


def agree_flag(label, full, h1, h2):
    d, t_ = full
    d1, _ = h1
    d2, _ = h2
    same_sign = np.sign(d1) == np.sign(d2) and np.sign(d1) == np.sign(d)
    mag = (min(abs(d1), abs(d2)) / max(abs(d1), abs(d2))) if max(abs(d1), abs(d2)) > 0 else np.nan
    agree.append(dict(finding=label, full_spread=d, full_t_nw=t_, h1_spread=d1, h2_spread=d2,
                      same_sign=bool(same_sign), mag_ratio_small_over_large=mag,
                      verdict="AGREE" if (same_sign and mag >= 0.5) else ("SIGN_ONLY" if same_sign else "DISAGREE")))


for h in (5, 21):
    agree_flag(f"A_DIX_top-bottom_fwd{h}", spreadsA[(h, "FULL")], spreadsA[(h, "H1_2011_2018")], spreadsA[(h, "H2_2019_2026")])
for tgt in ("abs1", "rv5", "ratio5", "excess_var"):
    agree_flag(f"B_GEXsign_neg-pos_{tgt}", spreadsB[("sign", tgt, "FULL")], spreadsB[("sign", tgt, "H1_2011_2018")], spreadsB[("sign", tgt, "H2_2019_2026")])
    agree_flag(f"B_GEXq0-q4_{tgt}", spreadsB[("q", tgt, "FULL")], spreadsB[("q", tgt, "H1_2011_2018")], spreadsB[("q", tgt, "H2_2019_2026")])
for h in (5, 21):
    agree_flag(f"C_interaction_fwd{h}", spreadsC[(h, "FULL")], spreadsC[(h, "H1_2011_2018")], spreadsC[(h, "H2_2019_2026")])
for tgt in ("fwd5", "fwd21", "rv5", "ratio5"):
    agree_flag(f"D_inversion_{tgt}", spreadsD[(tgt, "FULL_2016_2026")], spreadsD[(tgt, "H1_2016_2020")], spreadsD[(tgt, "H2_2021_2026")])
agree_df = pd.DataFrame(agree)

# ------------------------------------------------------------------ Strategy from the single strongest finding
# Pre-registered rule: pick the test with the largest |t_nw| on the full sample across A, B, C, D.
# If it is a vol finding (B or D vol targets) trade it as a vol filter: long SPX when the signal is in its
# low-vol bucket, flat in the high-vol bucket. If it is a return finding, long in the favorable bucket, else flat.
cands = {
    "A_DIX_fwd5": (spreadsA[(5, "FULL")][1], "dix_dec", lambda s: s["dix_dec"] == 9, "long when DIX in top trailing-252 decile"),
    "A_DIX_fwd21": (spreadsA[(21, "FULL")][1], "dix_dec", lambda s: s["dix_dec"] == 9, "long when DIX in top trailing-252 decile"),
    "B_GEXq_abs1_volfilter": (spreadsB[("q", "abs1", "FULL")][1], "gex_q", lambda s: s["gex_q"] >= 1, "long SPX unless GEX in bottom trailing-252 quintile (vol filter)"),
    "B_GEXq_rv5_volfilter": (spreadsB[("q", "rv5", "FULL")][1], "gex_q", lambda s: s["gex_q"] >= 1, "long SPX unless GEX in bottom trailing-252 quintile (vol filter)"),
    "B_GEXsign_abs1_volfilter": (spreadsB[("sign", "abs1", "FULL")][1], "gex_neg", lambda s: s["gex_neg"] == 0, "long SPX unless GEX negative (vol filter)"),
    "C_inter_fwd5": (spreadsC[(5, "FULL")][1], "gex_q", lambda s: (s["gex_q"] == 0) & (s["dix_dec"] >= 8), "long when GEX bottom quintile and DIX top quintile"),
    "C_inter_fwd21": (spreadsC[(21, "FULL")][1], "gex_q", lambda s: (s["gex_q"] == 0) & (s["dix_dec"] >= 8), "long when GEX bottom quintile and DIX top quintile"),
    "D_inv_fwd5": (spreadsD[("fwd5", "FULL_2016_2026")][1], "inv", lambda s: s["inv"] == 1, "long when vix9d > vix3m"),
    "D_inv_fwd21": (spreadsD[("fwd21", "FULL_2016_2026")][1], "inv", lambda s: s["inv"] == 1, "long when vix9d > vix3m"),
    "D_inv_rv5_volfilter": (spreadsD[("rv5", "FULL_2016_2026")][1], "inv", lambda s: s["inv"] == 0, "long SPX unless vix9d > vix3m (vol filter)"),
}
best = max(cands, key=lambda k: abs(cands[k][0]) if np.isfinite(cands[k][0]) else -1)
best_t, _, rule_fn, rule_txt = cands[best]
sign = 1.0  # rule lambdas already encode the favorable (long) bucket

s = df.dropna(subset=["r1"]).copy()
sig = rule_fn(s).astype(float)
sig = sig.where(s[cands[best][1]].notna())
pos = (sig if sign > 0 else 1 - sig).fillna(0).values  # position decided at close t, earns r1[t] = t->t+1
rt = s["r1"].values
pos_prev = np.concatenate([[0.0], pos[:-1]])
turn = np.abs(pos - pos_prev)
strat = pos * rt - COST * turn
bh = rt
valid = ~np.isnan(s[cands[best][1]].values)
strat = strat[valid]; bh = bh[valid]; dates_s = s["date"].values[valid]; turn = turn[valid]; posv = pos[valid]


def perf(r):
    r = np.asarray(r)
    ann = r.mean() * 252
    vol = r.std(ddof=1) * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else np.nan
    eq = np.exp(np.cumsum(r))
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return ann, vol, sharpe, dd


sa, sv, ss, sdd = perf(strat)
ba, bv, bs, bdd = perf(bh)
n_days = len(strat)
turnover_py = turn.sum() / (n_days / 252)
time_in = posv.mean()
strat_rows = [
    dict(strategy=f"{best}: {rule_txt}" + ("" if sign > 0 else " (inverted: long in complement)"), ann_ret=sa, ann_vol=sv, sharpe=ss, max_dd=sdd,
         time_in_market=time_in, round_trips_per_year=turnover_py / 2, n_days=n_days, cost_bp_per_side=1,
         start=str(pd.Timestamp(dates_s[0]).date()), end=str(pd.Timestamp(dates_s[-1]).date())),
    dict(strategy="buy_and_hold_SPX_same_window", ann_ret=ba, ann_vol=bv, sharpe=bs, max_dd=bdd, time_in_market=1.0,
         round_trips_per_year=0, n_days=n_days, cost_bp_per_side=0, start=str(pd.Timestamp(dates_s[0]).date()), end=str(pd.Timestamp(dates_s[-1]).date())),
]
# strategy by half
for pname, m in (("H1_2011_2018", pd.DatetimeIndex(dates_s) < half_cut), ("H2_2019_2026", pd.DatetimeIndex(dates_s) >= half_cut)):
    if m.sum() > 50:
        a_, v_, s_, d_ = perf(strat[m]); a2, v2, s2, d2 = perf(bh[m])
        strat_rows.append(dict(strategy=f"{best} {pname}", ann_ret=a_, ann_vol=v_, sharpe=s_, max_dd=d_, time_in_market=posv[m].mean(),
                               round_trips_per_year=turn[m].sum() / (m.sum() / 252) / 2, n_days=int(m.sum()), cost_bp_per_side=1,
                               start="", end=""))
        strat_rows.append(dict(strategy=f"buy_and_hold {pname}", ann_ret=a2, ann_vol=v2, sharpe=s2, max_dd=d2, time_in_market=1.0,
                               round_trips_per_year=0, n_days=int(m.sum()), cost_bp_per_side=0, start="", end=""))
strat_df = pd.DataFrame(strat_rows)

# ------------------------------------------------------------------ write outputs
res = pd.DataFrame(rows)
res.to_csv(os.path.join(OUT, "gex_dix_results.csv"), index=False)
agree_df.to_csv(os.path.join(OUT, "gex_dix_half_agreement.csv"), index=False)
strat_df.to_csv(os.path.join(OUT, "gex_dix_strategy.csv"), index=False)

# plot data: decile bars with per-bucket block-bootstrap 90% CIs (fwd21, full) and ratio5 by GEX quintile (full)
plot_rows = []
s = df.dropna(subset=["dix_dec", "fwd21"])
for b, grp in s.groupby("dix_dec"):
    y = grp["fwd21"].values
    bb = block_boot_means(y, 21)
    plot_rows.append(dict(panel="A: DIX decile (trailing 252d) vs fwd 21d SPX log return, 2011-2026", bucket=int(b),
                          value=y.mean() * 100, lo=np.percentile(bb, 5) * 100, hi=np.percentile(bb, 95) * 100, n=len(y)))
s = df.dropna(subset=["gex_q", "ratio5"])
for b, grp in s.groupby("gex_q"):
    y = grp["ratio5"].values
    bb = block_boot_means(y, 5)
    plot_rows.append(dict(panel="B: GEX quintile (trailing 252d) vs realized 5d vol / VIX, 2011-2026", bucket=int(b),
                          value=y.mean(), lo=np.percentile(bb, 5), hi=np.percentile(bb, 95), n=len(y)))
pd.DataFrame(plot_rows).to_csv(os.path.join(OUT, "gex_dix_plotdata.csv"), index=False)

# ------------------------------------------------------------------ console report
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 500)
print(f"rows={n} dates {df.date.min().date()} .. {df.date.max().date()}  first ranked day {df.dropna(subset=['dix_dec']).date.min().date()}")
print(f"GEX negative days: {int(df.gex_neg.sum())} ({df.gex_neg.mean():.1%}); inversion days: {int(np.nansum(df.inv))} of {int(df.inv.notna().sum())}")
print("\n=== Bucket means (FULL) ===")
print(res[(res.period.str.startswith("FULL")) & res.spread.isna() & res.bucket.str.contains("=")].to_string(index=False))
print("\n=== Spreads (all periods) ===")
sp = res[res.spread.notna()][["test", "period", "target", "bucket", "n_hi", "n_lo", "spread", "ci_lo", "ci_hi", "t_nw", "spearman", "p_spear"]]
print(sp.to_string(index=False))
print("\n=== Share RV>IV ===")
print(res[res.test == "B_share_RV_gt_IV"].to_string(index=False))
print("\n=== Half agreement ===")
print(agree_df.to_string(index=False))
print(f"\n=== Strategy (selected by max |t_nw| across all full-sample spread tests: {best}, t={best_t:.2f}) ===")
print(strat_df.to_string(index=False))
print(f"\nTotal hypotheses tested (full-sample primary tests): {len(HYP)}; with 2 halves each: {len(HYP) * 3}")
for h_ in HYP:
    print("  -", h_)
