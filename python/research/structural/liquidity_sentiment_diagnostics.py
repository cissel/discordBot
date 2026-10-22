import pandas as pd, numpy as np
ROOT="/home/jhcv/discordBot"
CACHE=f"{ROOT}/outputs/markets/cache"
OUT=f"{ROOT}/outputs/research/structural"

px = pd.read_csv(f"{CACHE}/spy_gex_daily.csv", parse_dates=["date"])[["date","price"]].sort_values("date")
px["ret1"]=px["price"].pct_change()
px["fwd_21"]=px["price"].shift(-21)/px["price"]-1
px["trail_21"]=px["price"]/px["price"].shift(21)-1

# --- H3 diagnostics: google trends fear ---
gt = pd.read_csv(f"{CACHE}/google_trends_daily.csv", parse_dates=["date"])
m3 = gt[["date","trends_fear_z21"]].dropna().merge(px[["date","fwd_21","trail_21"]], on="date", how="left").dropna()
print("H3 n=",len(m3), "span", m3.date.min(), m3.date.max())
print("corr(trends_fear_z21, trailing21 realized return):", m3["trends_fear_z21"].corr(m3["trail_21"]))
m3["q"]=pd.qcut(m3["trends_fear_z21"],5,labels=False,duplicates="drop")
print(m3.groupby("q")["fwd_21"].agg(["mean","count"]))
print("spearman fear vs fwd21:", m3["trends_fear_z21"].corr(m3["fwd_21"], method="spearman"))
beta = np.polyfit(m3["trail_21"], m3["fwd_21"],1)
resid = m3["fwd_21"] - (beta[0]*m3["trail_21"]+beta[1])
print("corr(fear_z21, fwd21 residual after removing trail_21 momentum):", m3["trends_fear_z21"].corr(resid))
m3["year"]=m3.date.dt.year
print("top quintile year counts:\n", m3[m3.q==m3.q.max()].year.value_counts().sort_index())
print("bottom quintile year counts:\n", m3[m3.q==m3.q.min()].year.value_counts().sort_index())

print()
print("====== H4 diagnostics: net liquidity ======")
walcl = pd.read_csv(f"{CACHE}/WALCL.csv", parse_dates=["date"]).rename(columns={"WALCL_val":"walcl"})
rrp = pd.read_csv(f"{CACHE}/RRPONTSYD.csv", parse_dates=["date"]).rename(columns={"RRPONTSYD_val":"rrp"})
tga = pd.read_csv(f"{CACHE}/WTREGEN.csv", parse_dates=["date"]).rename(columns={"WTREGEN_val":"tga"})
liq = walcl.merge(tga, on="date", how="inner")
liq = pd.merge_asof(liq.sort_values("date"), rrp.sort_values("date"), on="date", direction="backward")
liq["net_liq"]=liq["walcl"]-liq["rrp"]*1000-liq["tga"]
liq["net_liq_chg4w"]=liq["net_liq"].pct_change(4, fill_method=None)
liq["pub_date"]=liq["date"]+pd.Timedelta(days=3)
print("liq weekly rows:", len(liq), liq.date.min(), liq.date.max())
m4 = liq[["pub_date","net_liq_chg4w"]].dropna().rename(columns={"pub_date":"date"})
m4 = pd.merge_asof(m4.sort_values("date"), px[["date","fwd_21","trail_21"]].sort_values("date"), on="date", direction="forward").dropna()
print("H4 n=",len(m4), m4.date.min(), m4.date.max())
m4["q"]=pd.qcut(m4["net_liq_chg4w"],5,labels=False,duplicates="drop")
print(m4.groupby("q")["fwd_21"].agg(["mean","count"]))
print("spearman net_liq_chg4w vs fwd21:", m4["net_liq_chg4w"].corr(m4["fwd_21"], method="spearman"))
print("corr(net_liq_chg4w, trail_21):", m4["net_liq_chg4w"].corr(m4["trail_21"]))
beta2 = np.polyfit(m4["trail_21"], m4["fwd_21"],1)
resid2 = m4["fwd_21"] - (beta2[0]*m4["trail_21"]+beta2[1])
print("corr(net_liq_chg4w, fwd21 residual after trail21):", m4["net_liq_chg4w"].corr(resid2))
m4["year"]=m4.date.dt.year
print("top quintile year counts:\n", m4[m4.q==m4.q.max()].year.value_counts().sort_index())
print("bottom quintile year counts:\n", m4[m4.q==m4.q.min()].year.value_counts().sort_index())
import pandas as pd, numpy as np
ROOT="/home/jhcv/discordBot"
CACHE=f"{ROOT}/outputs/markets/cache"
OUT=f"{ROOT}/outputs/research/structural"

dA = pd.read_csv(f"{OUT}/liquidity_sentiment_strategyA.csv", parse_dates=["date"])
dA["year"] = dA["date"].dt.year
dA["pnl"] = dA["cum"].pct_change().fillna(dA["cum"]-1)
dA["bh_ret"] = dA["bh_cum"].pct_change().fillna(dA["bh_cum"]-1)

def yearly(d, col):
    out = {}
    for y, g in d.groupby("year"):
        v = g[col].std()*np.sqrt(252)
        out[y] = (g[col].mean()*252)/v if v>0 else np.nan
    return out

py_strat = yearly(dA, "pnl")
py_bh = yearly(dA, "bh_ret")
print("year  strat_sharpe  bh_sharpe  strat_beats_bh")
better = 0
for y in sorted(py_strat):
    b = py_strat[y] > py_bh[y]
    better += b
    print(f"{y}  {py_strat[y]:.2f}  {py_bh[y]:.2f}  {b}")
print(f"\nstrategy beats B&H in {better}/{len(py_strat)} years")

# worst month comparison
dA["ym"] = dA["date"].dt.to_period("M")
mret = dA.groupby("ym").apply(lambda g: (1+g["pnl"]).prod()-1, include_groups=False)
mret_bh = dA.groupby("ym").apply(lambda g: (1+g["bh_ret"]).prod()-1, include_groups=False)
print("\nworst month strat:", mret.min(), mret.idxmin())
print("worst month B&H  :", mret_bh.min(), mret_bh.idxmin())

# turnover / trades per year
dA["sig_chg"] = dA["sig"].diff().abs().fillna(0)
trades_per_year = dA.groupby("year")["sig_chg"].sum().mean()
print("\navg position-flips per year:", trades_per_year)
print("total flips over full sample:", dA["sig_chg"].sum(), "over", dA['year'].nunique(), "years")

# sensitivity to cost: already 1bp baked in cum, check raw flip count effect at 5bp
flips = dA["sig_chg"].sum()
extra_cost_5bp = flips * 4/1e4  # already paid 1bp, adding 4bp more per side
print(f"total extra cost impact if cost rises to 5bp/side: {extra_cost_5bp*100:.2f}% cumulative over {dA['year'].nunique()} yrs")
import pandas as pd, numpy as np
ROOT="/home/jhcv/discordBot"
OUT=f"{ROOT}/outputs/research/structural"

dA = pd.read_csv(f"{OUT}/liquidity_sentiment_strategyA.csv", parse_dates=["date"])
dA = dA[dA["date"] >= "2015-01-01"].reset_index(drop=True)
dA["pnl"] = dA["cum"].pct_change().fillna(dA["cum"].iloc[0]-1)
dA["bh_ret"] = dA["bh_cum"].pct_change().fillna(dA["bh_cum"].iloc[0]-1)
dA["cum"] = (1+dA["pnl"]).cumprod()
dA["bh_cum"] = (1+dA["bh_ret"]).cumprod()

ann_ret = dA["pnl"].mean()*252
ann_vol = dA["pnl"].std()*np.sqrt(252)
sharpe = ann_ret/ann_vol
bh_ret = dA["bh_ret"].mean()*252
bh_vol = dA["bh_ret"].std()*np.sqrt(252)
bh_sharpe = bh_ret/bh_vol
dd = (dA["cum"]/dA["cum"].cummax()-1).min()
bh_dd = (dA["bh_cum"]/dA["bh_cum"].cummax()-1).min()
print(f"2015-2026 only (real signal window): strat ann_ret={ann_ret*100:.2f}% sharpe={sharpe:.3f} maxdd={dd*100:.2f}%")
print(f"                                     B&H   ann_ret={bh_ret*100:.2f}% sharpe={bh_sharpe:.3f} maxdd={bh_dd*100:.2f}%")

mid = len(dA)//2
h1, h2 = dA.iloc[:mid], dA.iloc[mid:]
def sh(x, col):
    v = x[col].std()*np.sqrt(252)
    return (x[col].mean()*252)/v if v>0 else np.nan
print(f"H1 strat={sh(h1,'pnl'):.3f} bh={sh(h1,'bh_ret'):.3f} | H2 strat={sh(h2,'pnl'):.3f} bh={sh(h2,'bh_ret'):.3f}")
print("H1 span", h1.date.min(), h1.date.max(), "H2 span", h2.date.min(), h2.date.max())

dA.to_csv(f"{OUT}/liquidity_sentiment_strategyA_2015plus.csv", index=False)
print("rows:", len(dA))
