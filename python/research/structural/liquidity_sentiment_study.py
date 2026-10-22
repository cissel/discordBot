#!/usr/bin/env python3
"""Structural-edge sweep #2: liquidity, sentiment, credit and order-flow signals
that the Oct 19-20 structural study (calendar/GEX/DIX/VIX-term/overnight/block/
skew/trend/VRP) did not cover.

Pre-registered hypotheses (fixed before any result was looked at):
  H1  AAII bull-bear spread (weekly survey, contrarian) -> SPX fwd 21d/63d return
  H2  CNN Fear & Greed index (daily) -> SPX fwd 21d return, extreme deciles
  H3  Google Trends "risk appetite" / "fear" composite -> SPX fwd 21d return
  H4  Fed net liquidity (WALCL - RRP - TGA), weekly % chg, 3-day publication lag -> SPX fwd 21d return
  H5  ICE BofA HY OAS credit spread, daily chg / widening -> SPX fwd 5d return AND fwd 5d realized vol
  H6  SPY order-flow CVD (Lee-Ready), daily z-score extremes -> next-day return (mean reversion)

Inference matches the house convention (gex_dix_study.py / vrp_regime_study.py):
  - NW-HAC t-stats, lag = horizon - 1 for overlapping forward windows
  - Both-halves agreement (split at the sample midpoint) required to pass
  - 10,000-draw circular block bootstrap, 90% CI, block = horizon
  - Pass rule stated per hypothesis before results are read
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
RNG = np.random.default_rng(42)
NBOOT = 10000


def nw_t(x, lags):
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
    return float(m / np.sqrt(max(s, 1e-12) / n))


def block_boot_mean(y, blk, nb=NBOOT):
    y = np.asarray(y, float)
    y = y[~np.isnan(y)]
    m = len(y)
    if m < 10:
        return np.array([np.nan])
    blk = max(1, min(blk, m))
    nblk = int(np.ceil(m / blk))
    starts = RNG.integers(0, m, size=(nb, nblk))
    idx = (starts[:, :, None] + np.arange(blk)[None, None, :]) % m
    idx = idx.reshape(nb, -1)[:, :m]
    return y[idx].mean(axis=1)


def ci90(y, blk):
    b = block_boot_mean(y, blk)
    return np.nanpercentile(b, 5), np.nanpercentile(b, 95)


def halves(df):
    mid = len(df) // 2
    return df.iloc[:mid], df.iloc[mid:]


# ---------------------------------------------------------------------------
# Base price series: SPX close from spy_gex_daily.csv (2011-05-02 .. present),
# same series used by gex_dix_study.py / vrp_regime_study.py / holdout study.
# ---------------------------------------------------------------------------
px = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"])
px = px[["date", "price"]].sort_values("date").reset_index(drop=True)
px["ret1"] = px["price"].pct_change()
for h in (5, 21, 63):
    px[f"fwd_{h}"] = px["price"].shift(-h) / px["price"] - 1
# realized vol over next h days (annualized), for H5
for h in (5,):
    px[f"rv_fwd_{h}"] = px["ret1"].shift(-h).rolling(h).std().shift(-(h - 1)) * np.sqrt(252) * 100

results = []
notes = []

# ---------------------------------------------------------------------------
# H1: AAII bull-bear spread (weekly survey) contrarian
# ---------------------------------------------------------------------------
sent = pd.read_csv(os.path.join(CACHE, "sentiment_daily.csv"), parse_dates=["date"])
aaii = sent[["date", "aaii_bull_bear_spread"]].dropna().copy()
# survey is weekly and forward-filled daily in the cache; keep only the row where the
# value first changes (the actual release), to avoid pseudo-replicated daily rows
aaii = aaii[aaii["aaii_bull_bear_spread"].diff().fillna(1) != 0].reset_index(drop=True)
last_sent_date = sent["date"].max()
notes.append(f"sentiment_daily.csv last row: {last_sent_date.date()} (cron appears stale if this is far before today)")

m1 = aaii.merge(px[["date", "fwd_21", "fwd_63"]], on="date", how="left").dropna()
m1["decile"] = pd.qcut(m1["aaii_bull_bear_spread"], 5, labels=False, duplicates="drop")
lo = m1[m1["decile"] == m1["decile"].min()]
hi = m1[m1["decile"] == m1["decile"].max()]
for h in (21, 63):
    lag = max(h // 7 - 1, 1)  # weekly obs -> approx weeks per horizon for NW lag
    diff = lo[f"fwd_{h}"].mean() - hi[f"fwd_{h}"].mean()  # contrarian: low sentiment (bearish) should beat high (bullish)
    spread = lo[f"fwd_{h}"].values - np.full(len(lo), hi[f"fwd_{h}"].mean())
    t = nw_t(spread, lag)
    cilo, cihi = ci90(spread, lag)
    h1a, h1b = halves(m1)
    def q_diff(sub):
        if len(sub) < 10:
            return np.nan
        d = pd.qcut(sub["aaii_bull_bear_spread"], 5, labels=False, duplicates="drop")
        lo_s = sub[d == d.min()][f"fwd_{h}"].mean()
        hi_s = sub[d == d.max()][f"fwd_{h}"].mean()
        return lo_s - hi_s
    d_h1, d_h2 = q_diff(h1a), q_diff(h1b)
    results.append(dict(hyp="H1_aaii", cell=f"fwd_{h}d bottom-top quintile", n=len(lo) + len(hi),
                         diff_bps=diff * 1e4, nw_t=t, ci_lo_bps=cilo * 1e4, ci_hi_bps=cihi * 1e4,
                         diff_H1_bps=d_h1 * 1e4 if pd.notna(d_h1) else np.nan,
                         diff_H2_bps=d_h2 * 1e4 if pd.notna(d_h2) else np.nan))

# ---------------------------------------------------------------------------
# H2: CNN Fear & Greed extremes, contrarian
# ---------------------------------------------------------------------------
cfg = sent[["date", "cfg_value"]].dropna().copy()
m2 = cfg.merge(px[["date", "fwd_21"]], on="date", how="left").dropna()
extreme_fear = m2[m2["cfg_value"] <= 25]
extreme_greed = m2[m2["cfg_value"] >= 75]
diff2 = extreme_fear["fwd_21"].mean() - extreme_greed["fwd_21"].mean()
spread2 = np.concatenate([extreme_fear["fwd_21"].values - extreme_greed["fwd_21"].mean()])
t2 = nw_t(spread2, 20)
ci2 = ci90(spread2, 21)
m2a, m2b = halves(m2)
def fg_diff(sub):
    ef = sub[sub["cfg_value"] <= 25]["fwd_21"].mean()
    eg = sub[sub["cfg_value"] >= 75]["fwd_21"].mean()
    return ef - eg
results.append(dict(hyp="H2_feargreed", cell="extreme_fear(<=25) minus extreme_greed(>=75) fwd21",
                     n=len(extreme_fear) + len(extreme_greed), diff_bps=diff2 * 1e4, nw_t=t2,
                     ci_lo_bps=ci2[0] * 1e4, ci_hi_bps=ci2[1] * 1e4,
                     diff_H1_bps=fg_diff(m2a) * 1e4, diff_H2_bps=fg_diff(m2b) * 1e4))

# ---------------------------------------------------------------------------
# H3: Google Trends risk-appetite / fear composite
# ---------------------------------------------------------------------------
gt = pd.read_csv(os.path.join(CACHE, "google_trends_daily.csv"), parse_dates=["date"])
gt_last = gt["date"].max()
notes.append(f"google_trends_daily.csv last row: {gt_last.date()}")
m3 = gt[["date", "trends_fear_z21"]].dropna().merge(px[["date", "fwd_21"]], on="date", how="left").dropna()
m3["decile"] = pd.qcut(m3["trends_fear_z21"], 5, labels=False, duplicates="drop")
lo3 = m3[m3["decile"] == m3["decile"].max()]  # high fear search volume = contrarian bullish
hi3 = m3[m3["decile"] == m3["decile"].min()]
diff3 = lo3["fwd_21"].mean() - hi3["fwd_21"].mean()
spread3 = lo3["fwd_21"].values - np.full(len(lo3), hi3["fwd_21"].mean())
t3 = nw_t(spread3, 20)
ci3 = ci90(spread3, 21)
m3a, m3b = halves(m3)
def gt_diff(sub):
    d = pd.qcut(sub["trends_fear_z21"], 5, labels=False, duplicates="drop")
    return sub[d == d.max()]["fwd_21"].mean() - sub[d == d.min()]["fwd_21"].mean()
results.append(dict(hyp="H3_googletrends_fear", cell="top-bottom quintile fear search, fwd21",
                     n=len(lo3) + len(hi3), diff_bps=diff3 * 1e4, nw_t=t3,
                     ci_lo_bps=ci3[0] * 1e4, ci_hi_bps=ci3[1] * 1e4,
                     diff_H1_bps=gt_diff(m3a) * 1e4, diff_H2_bps=gt_diff(m3b) * 1e4))

# ---------------------------------------------------------------------------
# H4: Fed net liquidity (WALCL - RRP - TGA), weekly % change, 3-day pub lag
# ---------------------------------------------------------------------------
walcl = pd.read_csv(os.path.join(CACHE, "WALCL.csv"), parse_dates=["date"]).rename(columns={"WALCL_val": "walcl"})
rrp = pd.read_csv(os.path.join(CACHE, "RRPONTSYD.csv"), parse_dates=["date"]).rename(columns={"RRPONTSYD_val": "rrp"})
tga = pd.read_csv(os.path.join(CACHE, "WTREGEN.csv"), parse_dates=["date"]).rename(columns={"WTREGEN_val": "tga"})
liq = walcl.merge(tga, on="date", how="inner")
liq = pd.merge_asof(liq.sort_values("date"), rrp.sort_values("date"), on="date", direction="backward")
liq["net_liq"] = liq["walcl"] - liq["rrp"] * 1000 - liq["tga"]  # RRP in $bn daily avg, WALCL/TGA in $mn
liq["net_liq_chg4w"] = liq["net_liq"].pct_change(4)  # ~4 weekly obs = 1 month
liq["pub_date"] = liq["date"] + pd.Timedelta(days=3)  # publication lag, avoid lookahead
m4 = liq[["pub_date", "net_liq_chg4w"]].dropna().rename(columns={"pub_date": "date"})
m4 = pd.merge_asof(m4.sort_values("date"), px[["date", "fwd_21"]].sort_values("date"), on="date", direction="forward")
m4 = m4.dropna()
m4["decile"] = pd.qcut(m4["net_liq_chg4w"], 5, labels=False, duplicates="drop")
lo4 = m4[m4["decile"] == m4["decile"].min()]
hi4 = m4[m4["decile"] == m4["decile"].max()]
diff4 = hi4["fwd_21"].mean() - lo4["fwd_21"].mean()
spread4 = hi4["fwd_21"].values - np.full(len(hi4), lo4["fwd_21"].mean())
t4 = nw_t(spread4, 4)
ci4 = ci90(spread4, 4)
m4a, m4b = halves(m4)
def liq_diff(sub):
    d = pd.qcut(sub["net_liq_chg4w"], 5, labels=False, duplicates="drop")
    return sub[d == d.max()]["fwd_21"].mean() - sub[d == d.min()]["fwd_21"].mean()
results.append(dict(hyp="H4_net_liquidity", cell="top-bottom quintile 4w net-liq chg, fwd21",
                     n=len(lo4) + len(hi4), diff_bps=diff4 * 1e4, nw_t=t4,
                     ci_lo_bps=ci4[0] * 1e4, ci_hi_bps=ci4[1] * 1e4,
                     diff_H1_bps=liq_diff(m4a) * 1e4, diff_H2_bps=liq_diff(m4b) * 1e4))

# ---------------------------------------------------------------------------
# H5: HY OAS credit spread widening -> fwd 5d return and fwd 5d realized vol
# (only fetchable history on this network: 2023-10-23 .. present, ~3 yrs -> short sample, flagged)
# ---------------------------------------------------------------------------
hy = pd.read_csv(os.path.join(CACHE, "BAMLH0A0HYM2_full.csv"), parse_dates=["date"]).rename(columns={"BAMLH0A0HYM2_val": "hy_oas"})
hy["hy_chg5d"] = hy["hy_oas"].diff(5)
m5 = hy[["date", "hy_chg5d"]].dropna().merge(px[["date", "fwd_5", "rv_fwd_5"]], on="date", how="left").dropna()
m5["decile"] = pd.qcut(m5["hy_chg5d"], 5, labels=False, duplicates="drop")
widen = m5[m5["decile"] == m5["decile"].max()]
tighten = m5[m5["decile"] == m5["decile"].min()]
diff5r = widen["fwd_5"].mean() - tighten["fwd_5"].mean()
diff5v = widen["rv_fwd_5"].mean() - tighten["rv_fwd_5"].mean()
t5r = nw_t(widen["fwd_5"].values - np.full(len(widen), tighten["fwd_5"].mean()), 4)
t5v = nw_t(widen["rv_fwd_5"].values - np.full(len(widen), tighten["rv_fwd_5"].mean()), 4)
ci5r = ci90(widen["fwd_5"].values - np.full(len(widen), tighten["fwd_5"].mean()), 5)
ci5v = ci90(widen["rv_fwd_5"].values - np.full(len(widen), tighten["rv_fwd_5"].mean()), 5)
m5a, m5b = halves(m5)
def hy_diff(sub, col):
    d = pd.qcut(sub["hy_chg5d"], 5, labels=False, duplicates="drop")
    return sub[d == d.max()][col].mean() - sub[d == d.min()][col].mean()
results.append(dict(hyp="H5_hy_credit_ret", cell="top-bottom quintile 5d HY OAS chg, fwd5 RET (SHORT SAMPLE 2023-2026)",
                     n=len(widen) + len(tighten), diff_bps=diff5r * 1e4, nw_t=t5r,
                     ci_lo_bps=ci5r[0] * 1e4, ci_hi_bps=ci5r[1] * 1e4,
                     diff_H1_bps=hy_diff(m5a, "fwd_5") * 1e4, diff_H2_bps=hy_diff(m5b, "fwd_5") * 1e4))
results.append(dict(hyp="H5_hy_credit_vol", cell="top-bottom quintile 5d HY OAS chg, fwd5 RV (SHORT SAMPLE 2023-2026)",
                     n=len(widen) + len(tighten), diff_bps=diff5v * 100, nw_t=t5v,
                     ci_lo_bps=ci5v[0] * 100, ci_hi_bps=ci5v[1] * 100,
                     diff_H1_bps=hy_diff(m5a, "rv_fwd_5") * 100, diff_H2_bps=hy_diff(m5b, "rv_fwd_5") * 100))

# ---------------------------------------------------------------------------
# H6: order-flow CVD extremes -> next-day mean reversion (2021-2026)
# ---------------------------------------------------------------------------
of = pd.read_csv(os.path.join(ROOT, "outputs/features/markets/spy_orderflow_features.csv"), parse_dates=["date"])
sess = pd.read_csv(os.path.join(OUT, "spy_daily_sessions.csv"), parse_dates=["date"])
sess["ret_next"] = sess["r_close_to_close"].shift(-1)
m6 = of[["date", "cvd_z21"]].dropna().merge(sess[["date", "ret_next"]], on="date", how="left").dropna()
m6["decile"] = pd.qcut(m6["cvd_z21"], 5, labels=False, duplicates="drop")
buy_extreme = m6[m6["decile"] == m6["decile"].max()]   # heavy net buying -> reversion would be lower next-day
sell_extreme = m6[m6["decile"] == m6["decile"].min()]  # heavy net selling -> reversion would be higher next-day
diff6 = sell_extreme["ret_next"].mean() - buy_extreme["ret_next"].mean()  # reversion sign
t6 = nw_t(sell_extreme["ret_next"].values - np.full(len(sell_extreme), buy_extreme["ret_next"].mean()), 1)
ci6 = ci90(sell_extreme["ret_next"].values - np.full(len(sell_extreme), buy_extreme["ret_next"].mean()), 1)
m6a, m6b = halves(m6)
def cvd_diff(sub):
    d = pd.qcut(sub["cvd_z21"], 5, labels=False, duplicates="drop")
    return sub[d == d.min()]["ret_next"].mean() - sub[d == d.max()]["ret_next"].mean()
results.append(dict(hyp="H6_cvd_reversion", cell="sell-extreme minus buy-extreme next-day ret (SHORT SAMPLE 2021-2026)",
                     n=len(buy_extreme) + len(sell_extreme), diff_bps=diff6 * 1e4, nw_t=t6,
                     ci_lo_bps=ci6[0] * 1e4, ci_hi_bps=ci6[1] * 1e4,
                     diff_H1_bps=cvd_diff(m6a) * 1e4, diff_H2_bps=cvd_diff(m6b) * 1e4))

res = pd.DataFrame(results)


def verdict(row):
    ci_excl_zero = (row["ci_lo_bps"] > 0) or (row["ci_hi_bps"] < 0)
    same_sign_halves = np.sign(row["diff_H1_bps"]) == np.sign(row["diff_H2_bps"]) == np.sign(row["diff_bps"])
    strong_t = abs(row["nw_t"]) >= 2.0
    if ci_excl_zero and same_sign_halves and strong_t:
        return "PASS"
    if same_sign_halves and abs(row["nw_t"]) >= 1.5:
        return "MARGINAL"
    return "FAIL"


res["verdict"] = res.apply(verdict, axis=1)
res.to_csv(os.path.join(OUT, "liquidity_sentiment_results.csv"), index=False)

m = len(res)
with open(os.path.join(OUT, "liquidity_sentiment_summary.md"), "w") as f:
    f.write("# Liquidity / sentiment / credit / order-flow structural sweep (new hypotheses, not in the Oct 19-20 study)\n\n")
    f.write(f"{m} pre-registered hypotheses. Pass rule: 90% block-bootstrap CI excludes zero, both halves agree in sign "
            f"with the full-sample sign, AND |NW t| >= 2.0. Bonferroni m={m}: raw p must be below {0.10/m:.4f} to survive "
            f"a 90%-family correction.\n\n")
    f.write(res.to_markdown(index=False, floatfmt=".3f"))
    f.write("\n\nData notes:\n")
    for n in notes:
        f.write(f"- {n}\n")
    f.write("- H5 (credit spreads): FRED fredgraph.csv on this network only serves BAMLH0A0HYM2/BAMLC0A0CM from "
            "2023-10-23 forward regardless of requested start date (confirmed by direct curl with cosd=1990-01-01); "
            "this is a ~3-year sample, underpowered relative to the other tests here. Treat as exploratory only.\n")
    f.write("- H6 (order flow): spy_orderflow_features.csv only covers 2021-2026 (5 years); half-split is 2021-2023 vs 2024-2026.\n")
    passes = res[res["verdict"] == "PASS"]
    f.write(f"\nResult: {len(passes)}/{m} hypotheses pass all three conditions.\n")
    if len(passes):
        f.write(passes.to_markdown(index=False, floatfmt=".3f"))

print(res.to_string())
print("\nWrote outputs/research/structural/liquidity_sentiment_{results.csv,summary.md}")
