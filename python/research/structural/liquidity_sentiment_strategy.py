import pandas as pd, numpy as np
ROOT="/home/jhcv/discordBot"
CACHE=f"{ROOT}/outputs/markets/cache"
OUT=f"{ROOT}/outputs/research/structural"

px = pd.read_csv(f"{CACHE}/spy_gex_daily.csv", parse_dates=["date"])[["date","price"]].sort_values("date").reset_index(drop=True)
px["ret1"] = px["price"].pct_change()

def perf(sig, ret, dates, cost_bp=1.0, label=""):
    d = pd.DataFrame({"sig": sig, "ret": ret, "date": dates}).dropna().reset_index(drop=True)
    d["turn"] = d["sig"].diff().abs().fillna(d["sig"].abs())
    d["pnl"] = d["sig"] * d["ret"] - d["turn"] * cost_bp / 1e4
    ann_ret = d["pnl"].mean() * 252
    ann_vol = d["pnl"].std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
    cum = (1 + d["pnl"]).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    bh_cum = (1 + d["ret"]).cumprod()
    bh_sharpe = (d["ret"].mean()*252) / (d["ret"].std()*np.sqrt(252))
    n = len(d)
    mid = n // 2
    h1 = d.iloc[:mid]; h2 = d.iloc[mid:]
    def sh(x):
        v = x["pnl"].std()*np.sqrt(252)
        return (x["pnl"].mean()*252)/v if v > 0 else np.nan
    def bsh(x):
        v = x["ret"].std()*np.sqrt(252)
        return (x["ret"].mean()*252)/v if v > 0 else np.nan
    print(f"{label}: ann_ret={ann_ret*100:.2f}% sharpe={sharpe:.3f} (B&H {bh_sharpe:.3f}) max_dd={dd*100:.2f}% "
          f"H1_sharpe={sh(h1):.3f}(bh {bsh(h1):.3f}) H2_sharpe={sh(h2):.3f}(bh {bsh(h2):.3f}) n={n} frac_long={d['sig'].mean():.2f}")
    d["cum"] = cum.values
    d["bh_cum"] = bh_cum.values
    return d

print("=== Strategy A: Google Trends fear overlay (flat when fear_z21 top quintile, else long) ===")
gt = pd.read_csv(f"{CACHE}/google_trends_daily.csv", parse_dates=["date"])[["date","trends_fear_z21"]].dropna().sort_values("date")
gt["q_trail"] = gt["trends_fear_z21"].expanding(min_periods=250).apply(lambda s: pd.Series(s).rank(pct=True).iloc[-1])
m = px.merge(gt, on="date", how="left")
m["fear_q_lag"] = m["q_trail"].shift(1)
m["sig_A"] = np.where(m["fear_q_lag"] >= 0.8, 0.0, 1.0)
m["sig_A"] = m["sig_A"].fillna(1.0)
dA = perf(m["sig_A"], m["ret1"], m["date"], cost_bp=1.0, label="A: flat top-quintile-fear vs always-long")

print()
print("=== Strategy B: net-liquidity overlay (flat when liquidity contracting bottom quintile, else long) ===")
walcl = pd.read_csv(f"{CACHE}/WALCL.csv", parse_dates=["date"]).rename(columns={"WALCL_val":"walcl"})
rrp = pd.read_csv(f"{CACHE}/RRPONTSYD.csv", parse_dates=["date"]).rename(columns={"RRPONTSYD_val":"rrp"})
tga = pd.read_csv(f"{CACHE}/WTREGEN.csv", parse_dates=["date"]).rename(columns={"WTREGEN_val":"tga"})
liq = walcl.merge(tga, on="date", how="inner")
liq = pd.merge_asof(liq.sort_values("date"), rrp.sort_values("date"), on="date", direction="backward")
liq["net_liq"] = liq["walcl"] - liq["rrp"]*1000 - liq["tga"]
liq["net_liq_chg4w"] = liq["net_liq"].pct_change(4, fill_method=None)
liq["pub_date"] = liq["date"] + pd.Timedelta(days=3)
liq["q_trail"] = liq["net_liq_chg4w"].expanding(min_periods=52).apply(lambda s: pd.Series(s).rank(pct=True).iloc[-1])
liq_s = liq[["pub_date","q_trail"]].rename(columns={"pub_date":"date"}).dropna().sort_values("date")
m2 = pd.merge_asof(px.sort_values("date"), liq_s, on="date", direction="backward")
m2["sig_B"] = np.where(m2["q_trail"] <= 0.2, 0.0, 1.0)
m2["sig_B"] = m2["sig_B"].fillna(1.0)
dB = perf(m2["sig_B"], m2["ret1"], m2["date"], cost_bp=1.0, label="B: flat bottom-quintile-liquidity vs always-long")

print()
print("=== Combined: flat if EITHER extreme-fear-search OR contracting-liquidity ===")
mc = m[["date","sig_A"]].merge(m2[["date","sig_B"]], on="date", how="inner")
mc["sig_C"] = np.minimum(mc["sig_A"], mc["sig_B"])
mc = mc.merge(px[["date","ret1"]], on="date", how="left")
dC = perf(mc["sig_C"], mc["ret1"], mc["date"], cost_bp=1.0, label="C: combined flat-either-signal")

dA[["date","sig","pnl","cum","bh_cum"]].to_csv(f"{OUT}/liquidity_sentiment_strategyA.csv", index=False)
dB[["date","sig","pnl","cum","bh_cum"]].to_csv(f"{OUT}/liquidity_sentiment_strategyB.csv", index=False)
dC[["date","sig","pnl","cum","bh_cum"]].to_csv(f"{OUT}/liquidity_sentiment_strategyC.csv", index=False)
print("wrote strategy csvs")
