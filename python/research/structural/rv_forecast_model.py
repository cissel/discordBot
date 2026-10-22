#!/usr/bin/env python3
"""
rv_forecast_model.py  (pre-registered, Oct 19 2026)
=====================================================
Can a small regression replace the 5-bucket GEX lookup that production uses to
turn VIX into an expected 21-day realised vol (position sizing, vol-book entry)?

TARGET
  y_t = log RV_fwd(t), RV_fwd(t) = annualised std (ddof=1) of daily SPY log
  returns over trading days t+1..t+21, times sqrt(252) times 100 (vol points).
  Ratio form RV_fwd / VIX_t is reported alongside. VIX_t is the same-day close
  (known at the close when the forecast is made). Everything that is a regime
  or explanatory variable sourced from other files (GEX quintile, term slope,
  inversion, VVIX) is forward-filled to SPY dates and then lagged ONE day.
  Trailing realised vols use returns up to and including day t.

CANDIDATES (all fit on identical folds)
  baseline  : VIX_t * ratio[gex_q(t-1)], ratio = {1:0.887, 2:0.786, 3:0.725,
              4:0.685, 5:0.598}, or 1.01 if VIX9D>VIX3M on t-1. Production rule.
  vix_only  : VIX_t (ratio 1).
  har       : OLS  y ~ log RV_1d + log RV_5d + log RV_22d + log VIX_t
  har_struct: har + GEX quintile dummies (q2..q5, lagged) + log(VIX3M/VIX9D)
              lagged + inverted dummy lagged + log VVIX lagged. The requested
              implied/realised spread log(VIX_t/RV_22d) is an exact linear
              combination of log VIX_t and log RV_22d already in the model, so
              it is omitted from the linear fits (it would be unidentified) and
              kept only as a derived split variable for the GBM.
  ridge     : har_struct features, standardised, Ridge with alpha chosen by
              5-fold TimeSeriesSplit CV INSIDE the training fold only
  gbm       : har_struct features + iv_rv_spread, GradientBoostingRegressor(
              max_depth=2, n_estimators=300, learning_rate=0.03, subsample=0.8,
              seed 42). Hyper-parameters fixed before fitting, never tuned.
  lookup_wf : REFERENCE ONLY, added after the first run (post hoc, not in the
              pass rule): the same 6-cell lookup with ratios re-estimated on
              each training fold (mean RV_fwd/VIX per cell). It shows how much
              of the production lookup's edge comes from its ratios having been
              calibrated on the full 2016-2026 sample, test years included.

EVALUATION (written before any model was fit)
  Expanding-window walk-forward, yearly refit, Y = 2018..2026. Training rows
  for year Y are all rows whose 21-day forward window ENDS before Jan 1 of Y
  (so no training target overlaps the test year). Test rows are dates in Y with
  a complete forward window. Metrics per model, pooled and per year:
    RMSE, MAE of log RV_fwd; OOS R^2 = 1 - SSE/SSE(train-fold mean); bias
    (mean pred - actual); Mincer-Zarnowitz slope (actual on pred, OLS).
  Diebold-Mariano t: NW (21 lags, Bartlett) t-stat on d_t = e_model^2 - e_base^2
  over the pooled OOS rows. Negative = model better than baseline.
  Economic sign test: short a synthetic 30-day variance swap, payoff
  (VIX^2 - RV_fwd^2)/(2 VIX) vol points per unit vega, ONLY on days where the
  model's predicted ratio RV_fwd/VIX < theta, theta = median predicted ratio on
  that year's TRAINING fold (in-sample predictions). Report mean P&L per
  eligible day, hit rate, eligible fraction, monthly Sharpe (every 21st day,
  three offsets averaged, zero on ineligible days) and worst calendar-month
  mean of the gated daily series. Baseline is gated the same way with its own
  training-fold median ratio.

PASS RULE (best non-baseline model = lowest pooled OOS RMSE among har,
har_struct, ridge, gbm; vix_only is a reference only)
  (1) pooled OOS RMSE of log RV_fwd at least 5 percent below the baseline
      lookup AND lower than baseline in at least 6 of the 9 years,
  (2) economic sign test monthly Sharpe higher than the baseline's.
  Both hold -> PASS. Exactly one -> PARTIAL (labelled PARTIAL-RMSE or
  PARTIAL-ECON). Neither -> FAIL, baseline stands.

SUB-SAMPLE CHECK (not part of the pass rule): 2022..2026 folds, har_struct
with and without 5-minute intraday RV (trailing 1d/5d/22d) from the 1-min files.

Outputs (outputs/research/structural/)
  rv_forecast_results.csv      per model x year metrics
  rv_forecast_pooled.csv       pooled metrics + economic test + DM t
  rv_forecast_predictions.csv  date, actual, baseline, each model, eligible flags
  rv_forecast_summary.md       verdict
  models/markets/spy/rv_forecast_<today>.pkl  only if PASS or PARTIAL-RMSE
Deterministic: seed 42, no randomness outside the GBM subsample.
"""
import os
import sys
import pickle
import datetime as dt

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingRegressor

SEED = 42
np.random.seed(SEED)
BASE = os.path.expanduser("~/discordBot")
CACHE = os.path.join(BASE, "outputs/markets/cache")
INTRA = os.path.join(BASE, "outputs/markets/intraday")
OUT = os.path.join(BASE, "outputs/research/structural")
MODEL_DIR = os.path.join(BASE, "models/markets/spy")
H = 21
YEARS = list(range(2018, 2027))
RATIO = {1: 0.887, 2: 0.786, 3: 0.725, 4: 0.685, 5: 0.598}
RATIO_INV = 1.01
RV_FLOOR = 1.0  # vol points; floors log RV_1d on flat days

HAR_FEATS = ["log_rv1", "log_rv5", "log_rv22", "log_vix"]
STRUCT_FEATS = HAR_FEATS + ["gq2", "gq3", "gq4", "gq5", "term_slope", "inverted", "log_vvix"]
GBM_FEATS = STRUCT_FEATS + ["iv_rv_spread"]
TODAY = dt.date.today()


def nw_t(x, lags=H):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x); m = x.mean(); u = x - m
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(m / np.sqrt(s / n))


# ----------------------------------------------------------------------------
# features (python/rvForecast.py carries an identical copy so production never
# imports a research script)
# ----------------------------------------------------------------------------
def build_features(cache=CACHE, today=TODAY):
    spy = pd.read_csv(os.path.join(cache, "SPY.csv"), parse_dates=["date"]).set_index("date")["SPY_ret"]
    spy = spy[spy.index.date < today]            # drop provisional intraday row
    vix = pd.read_csv(os.path.join(cache, "VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
    gex = pd.read_csv(os.path.join(cache, "spy_gex_daily.csv"), parse_dates=["date"]).set_index("date")["gex"]
    vt = pd.read_csv(os.path.join(cache, "vix_term_history.csv"), parse_dates=["date"]).set_index("date")
    d = pd.DataFrame({"spy_ret": spy})
    lr = np.log1p(d["spy_ret"])
    d["vix"] = vix.reindex(d.index, method="ffill")
    gq = gex.rolling(252, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    d["gex_q"] = (np.floor((gq * 5).clip(0, 4.999)) + 1).reindex(d.index, method="ffill").shift(1)
    vt_l = vt.reindex(d.index, method="ffill").shift(1)
    d["inverted"] = (vt_l["vix9d"] > vt_l["vix3m"]).astype(float).where(vt_l["vix9d"].notna())
    d["term_slope"] = np.log(vt_l["vix3m"] / vt_l["vix9d"])
    d["log_vvix"] = np.log(vt_l["vvix"])
    ann = np.sqrt(252) * 100
    d["rv1"] = (lr.abs() * ann).clip(lower=RV_FLOOR)
    d["rv5"] = np.sqrt((lr ** 2).rolling(5).mean()) * ann
    d["rv22"] = np.sqrt((lr ** 2).rolling(22).mean()) * ann
    d["log_rv1"] = np.log(d["rv1"]); d["log_rv5"] = np.log(d["rv5"]); d["log_rv22"] = np.log(d["rv22"])
    d["log_vix"] = np.log(d["vix"])
    d["iv_rv_spread"] = d["log_vix"] - d["log_rv22"]
    for q in (2, 3, 4, 5):
        d[f"gq{q}"] = (d["gex_q"] == q).astype(float)
    ratio = d["gex_q"].map(RATIO)
    d["baseline_ratio"] = np.where(d["inverted"] == 1, RATIO_INV, ratio)
    d["baseline"] = d["vix"] * d["baseline_ratio"]
    # forward target (excludes today)
    d["rv_fwd"] = lr.shift(-1).rolling(H).std().shift(-(H - 1)) * ann
    d["window_end"] = pd.Series(d.index, index=d.index).shift(-H)
    return d


def build_intraday_rv(years=range(2021, 2027)):
    """Daily 5-min realised vol (regular session), annualised vol points."""
    out = []
    for y in years:
        p = os.path.join(INTRA, f"SPY_{y}_1min.csv")
        if not os.path.exists(p):
            continue
        b = pd.read_csv(p, usecols=["date", "close", "session"])
        b = b[b["session"] == "regular"]
        ts = pd.to_datetime(b["date"], utc=True).dt.tz_convert("America/New_York")
        b = b.assign(day=ts.dt.date, ts=ts.dt.tz_localize(None)).set_index("ts")
        c5 = b.groupby("day")["close"].apply(lambda s: s.resample("5min").last().dropna())
        c5 = c5.reset_index(level=0)
        lr5 = np.log(c5["close"]).groupby(c5["day"]).diff()
        rv = (lr5 ** 2).groupby(c5["day"]).sum()
        out.append(rv)
    rv = pd.concat(out)
    rv.index = pd.to_datetime(rv.index)
    return np.sqrt(rv * 252) * 100


# ----------------------------------------------------------------------------
def fit_predict(kind, Xtr, ytr, Xte):
    if kind == "ols":
        m = LinearRegression().fit(Xtr, ytr)
    elif kind == "ridge":
        best, best_mse = None, np.inf
        for a in np.logspace(-2, 3, 11):
            mses = []
            for tri, vai in TimeSeriesSplit(5).split(Xtr):
                p = make_pipeline(StandardScaler(), Ridge(alpha=a)).fit(Xtr[tri], ytr[tri])
                mses.append(np.mean((p.predict(Xtr[vai]) - ytr[vai]) ** 2))
            if np.mean(mses) < best_mse:
                best_mse, best = np.mean(mses), a
        m = make_pipeline(StandardScaler(), Ridge(alpha=best)).fit(Xtr, ytr)
        m.alpha_chosen = best
    elif kind == "gbm":
        m = GradientBoostingRegressor(max_depth=2, n_estimators=300, learning_rate=0.03, subsample=0.8,
                                      random_state=SEED).fit(Xtr, ytr)
    else:
        raise ValueError(kind)
    return m, m.predict(Xtr), m.predict(Xte)


MODELS = {"har": ("ols", HAR_FEATS), "har_struct": ("ols", STRUCT_FEATS), "ridge": ("ridge", STRUCT_FEATS),
          "gbm": ("gbm", GBM_FEATS)}


def metrics(y, p, ybar_train):
    e = p - y
    sse = float((e ** 2).sum()); sst = float(((y - ybar_train) ** 2).sum())
    if len(y) > 2 and np.std(p) > 0:
        slope = float(np.polyfit(p, y, 1)[0])
    else:
        slope = np.nan
    return {"n": len(y), "rmse": float(np.sqrt((e ** 2).mean())), "mae": float(np.abs(e).mean()),
            "oos_r2": 1 - sse / sst, "bias": float(e.mean()), "mz_slope": slope}


def econ(pnl, elig):
    s = pnl.where(elig, 0.0)
    act = pnl[elig]
    mo = [s.iloc[k::H] for k in range(3)]
    sharpe = float(np.mean([m.mean() / m.std() * np.sqrt(12) for m in mo if m.std() > 0]))
    cal = s.groupby(s.index.to_period("M")).mean()
    return {"eligible_frac": float(elig.mean()), "n_elig": int(elig.sum()), "mean_pnl_volpts": float(act.mean()),
            "hit_rate": float((act > 0).mean()), "monthly_sharpe": sharpe, "worst_month_volpts": float(cal.min()),
            "nw_t_gated": nw_t(s.values)}


def walk_forward(d, models, years, label=""):
    """Returns predictions frame with log-space predictions and eligibility flags."""
    preds = []
    for Y in years:
        jan1 = pd.Timestamp(f"{Y}-01-01")
        tr = d[d["window_end"] < jan1]
        te = d[(d.index >= jan1) & (d.index < pd.Timestamp(f"{Y + 1}-01-01"))]
        if len(te) == 0:
            continue
        ytr = tr["y"].values
        out = te[["y", "vix", "rv_fwd", "baseline", "pnl"]].copy()
        out["year"] = Y
        out["ybar_train"] = ytr.mean()
        # baseline and vix-only thresholds (training-fold median of ratio)
        out["pred_baseline"] = np.log(te["baseline"])
        out["theta_baseline"] = float(np.median(tr["baseline_ratio"]))
        out["elig_baseline"] = te["baseline_ratio"] < out["theta_baseline"]
        out["pred_vix_only"] = np.log(te["vix"])
        # reference: lookup re-estimated on the training fold
        cell_tr = np.where(tr["inverted"] == 1, 0, tr["gex_q"]); cell_te = np.where(te["inverted"] == 1, 0, te["gex_q"])
        cell_ratio = (tr["rv_fwd"] / tr["vix"]).groupby(cell_tr).mean()
        r_tr = pd.Series(cell_tr).map(cell_ratio).values; r_te = pd.Series(cell_te).map(cell_ratio).values
        out["pred_lookup_wf"] = np.log(te["vix"].values * r_te)
        out["theta_lookup_wf"] = float(np.median(r_tr))
        out["elig_lookup_wf"] = r_te < out["theta_lookup_wf"]
        for k, (kind, feats) in models.items():
            m, ptr, pte = fit_predict(kind, tr[feats].values, ytr, te[feats].values)
            out[f"pred_{k}"] = pte
            theta = float(np.median(np.exp(ptr) / tr["vix"].values))
            out[f"theta_{k}"] = theta
            out[f"elig_{k}"] = (np.exp(pte) / te["vix"].values) < theta
            if k == "ridge":
                out["ridge_alpha"] = m.alpha_chosen
        preds.append(out)
        print(f"  {label}fold {Y}: train n={len(tr)} (through {tr.index.max().date()}), test n={len(te)}")
    return pd.concat(preds)


def summarise(P, model_names, years):
    rows = []
    for k in ["baseline", "vix_only", "lookup_wf"] + model_names:
        for Y in years + ["pooled"]:
            s = P if Y == "pooled" else P[P["year"] == Y]
            if len(s) == 0:
                continue
            mt = metrics(s["y"].values, s[f"pred_{k}"].values, s["ybar_train"].values)
            mt.update({"model": k, "year": Y})
            if k != "vix_only":
                mt.update(econ(s["pnl"], s[f"elig_{k}"].astype(bool)))
            if k != "baseline":
                dd = (s[f"pred_{k}"] - s["y"]) ** 2 - (s["pred_baseline"] - s["y"]) ** 2
                mt["dm_t_vs_baseline"] = nw_t(dd.values) if len(dd) > H + 5 else np.nan
                mt["rmse_vs_baseline_pct"] = 100 * (mt["rmse"] / metrics(s["y"].values, s["pred_baseline"].values, s["ybar_train"].values)["rmse"] - 1)
            rows.append(mt)
    return pd.DataFrame(rows)


def main():
    print(f"today={TODAY}")
    d = build_features()
    n0 = len(d)
    d = d.dropna(subset=["vix", "gex_q", "inverted", "term_slope", "log_vvix", "log_rv22", "baseline"])
    n1 = len(d)
    full = d.dropna(subset=["rv_fwd"]).copy()
    print(f"rows: {n0} SPY days, {n1} with complete features, {len(full)} with complete 21-day forward window "
          f"({full.index.min().date()} .. {full.index.max().date()})")
    full["y"] = np.log(full["rv_fwd"])
    full["pnl"] = (full["vix"] ** 2 - full["rv_fwd"] ** 2) / (2 * full["vix"])

    print("walk-forward (main):")
    P = walk_forward(full, MODELS, YEARS)
    names = list(MODELS)
    res = summarise(P, names, YEARS)
    pooled = res[res["year"] == "pooled"].set_index("model")
    per_year = res[res["year"] != "pooled"]

    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    cols = ["n", "rmse", "mae", "oos_r2", "bias", "mz_slope", "rmse_vs_baseline_pct", "dm_t_vs_baseline"]
    print("\nPOOLED 2018-2026 (log RV_fwd):")
    print(pooled[cols].round(4).to_string())
    ecols = ["eligible_frac", "n_elig", "mean_pnl_volpts", "hit_rate", "monthly_sharpe", "worst_month_volpts", "nw_t_gated"]
    print("\nECONOMIC sign test (short var swap when predicted ratio < training median):")
    print(pooled.loc[["baseline", "lookup_wf"] + names, ecols].round(3).to_string())
    print("\nPER-YEAR RMSE:")
    py = per_year.pivot(index="year", columns="model", values="rmse")[["baseline", "vix_only", "lookup_wf"] + names]
    print(py.round(4).to_string())
    wins = {k: int((py[k] < py["baseline"]).sum()) for k in ["lookup_wf"] + names}
    print(f"\nyears beating baseline (of {len(py)}): {wins}")

    # pass rule
    best = pooled.loc[names, "rmse"].idxmin()
    b_rmse = pooled.loc["baseline", "rmse"]; m_rmse = pooled.loc[best, "rmse"]
    c1 = (m_rmse <= 0.95 * b_rmse) and (wins[best] >= 6)
    c2 = pooled.loc[best, "monthly_sharpe"] > pooled.loc["baseline", "monthly_sharpe"]
    verdict = "PASS" if (c1 and c2) else ("PARTIAL-RMSE" if c1 else ("PARTIAL-ECON" if c2 else "FAIL"))
    print(f"\nbest non-baseline = {best}: RMSE {m_rmse:.4f} vs baseline {b_rmse:.4f} "
          f"({100 * (m_rmse / b_rmse - 1):+.1f}%), wins {wins[best]}/9, "
          f"Sharpe {pooled.loc[best, 'monthly_sharpe']:.3f} vs {pooled.loc['baseline', 'monthly_sharpe']:.3f}")
    print(f"condition 1 (RMSE -5% and >=6/9 years): {c1};  condition 2 (econ Sharpe): {c2}  ->  {verdict}")

    # ratio-form report
    ratio_rows = []
    for k in ["baseline", "vix_only", "lookup_wf"] + names:
        r_pred = np.exp(P[f"pred_{k}"]) / P["vix"]; r_act = P["rv_fwd"] / P["vix"]
        ratio_rows.append({"model": k, "mean_pred_ratio": r_pred.mean(), "mean_actual_ratio": r_act.mean(),
                           "rmse_ratio": float(np.sqrt(((r_pred - r_act) ** 2).mean())),
                           "rmse_volpts": float(np.sqrt(((np.exp(P[f"pred_{k}"]) - P["rv_fwd"]) ** 2).mean()))})
    ratio_df = pd.DataFrame(ratio_rows).set_index("model")
    print("\nRATIO form (RV_fwd / VIX) and vol-point errors, pooled:")
    print(ratio_df.round(4).to_string())

    # coefficient report for har_struct on the final fold
    jan = pd.Timestamp("2026-01-01")
    tr = full[full["window_end"] < jan]
    lin = LinearRegression().fit(tr[STRUCT_FEATS].values, tr["y"].values)
    sd = tr[STRUCT_FEATS].std()
    coef = pd.DataFrame({"coef": lin.coef_, "coef_x_sd": lin.coef_ * sd.values}, index=STRUCT_FEATS)
    print("\nhar_struct OLS coefficients (train through 2025), coef and coef x feature sd:")
    print(coef.round(4).to_string())

    # sub-sample check with intraday RV
    print("\nSUB-SAMPLE 2022-2026: har_struct with/without 5-min intraday RV")
    try:
        irv = build_intraday_rv()
        sub = full.copy()
        sub["irv1"] = irv.reindex(sub.index)
        sub["log_irv1"] = np.log(sub["irv1"].clip(lower=RV_FLOOR))
        sub["log_irv5"] = np.log(np.sqrt((sub["irv1"] ** 2).rolling(5).mean()))
        sub["log_irv22"] = np.log(np.sqrt((sub["irv1"] ** 2).rolling(22).mean()))
        sub = sub.dropna(subset=["log_irv1", "log_irv5", "log_irv22"])
        sub = sub[sub.index >= pd.Timestamp("2021-02-15")]
        print(f"  sub-sample rows {len(sub)} ({sub.index.min().date()} .. {sub.index.max().date()})")
        sub_models = {"har_struct": ("ols", STRUCT_FEATS), "har_struct_intra": ("ols", STRUCT_FEATS + ["log_irv1", "log_irv5", "log_irv22"])}
        Ps = walk_forward(sub, sub_models, list(range(2022, 2027)), label="sub ")
        rs = summarise(Ps, list(sub_models), list(range(2022, 2027)))
        rs_p = rs[rs["year"] == "pooled"].set_index("model")
        print(rs_p[cols].round(4).to_string())
        sub_txt = rs_p[cols].round(4).to_string()
    except Exception as e:  # keep main study alive
        print(f"  intraday check failed: {e}")
        sub_txt = f"intraday check failed: {e}"

    # write outputs
    res.to_csv(os.path.join(OUT, "rv_forecast_results.csv"), index=False)
    pooled_out = pooled.join(ratio_df).reset_index()
    pooled_out["wins_vs_baseline"] = pooled_out["model"].map(wins)
    pooled_out["verdict"] = verdict; pooled_out["best_model"] = best
    pooled_out.to_csv(os.path.join(OUT, "rv_forecast_pooled.csv"), index=False)
    Pout = P.copy()
    Pout["actual_rv_fwd"] = Pout["rv_fwd"]
    for k in ["baseline", "vix_only", "lookup_wf"] + names:
        Pout[f"rv_{k}"] = np.exp(Pout[f"pred_{k}"])
    Pout = Pout.drop(columns=["rv_fwd", "baseline"]).rename_axis("date").reset_index()
    Pout.to_csv(os.path.join(OUT, "rv_forecast_predictions.csv"), index=False)

    # final model if warranted
    model_file = None
    if verdict in ("PASS", "PARTIAL-RMSE"):
        kind, feats = MODELS[best]
        m, ptr, _ = fit_predict(kind, full[feats].values, full["y"].values, full[feats].values[:1])
        theta = float(np.median(np.exp(ptr) / full["vix"].values))
        os.makedirs(MODEL_DIR, exist_ok=True)
        model_file = os.path.join(MODEL_DIR, f"rv_forecast_{TODAY.isoformat()}.pkl")
        with open(model_file, "wb") as f:
            pickle.dump({"model": m, "kind": best, "features": feats, "target": "log RV_fwd (21d, annualised vol pts)",
                         "trained_through": str(full.index.max().date()), "n_train": len(full), "theta_ratio": theta,
                         "rv_floor": RV_FLOOR, "verdict": verdict, "pooled_rmse": m_rmse, "baseline_rmse": b_rmse,
                         "created": TODAY.isoformat()}, f)
        print(f"\nsaved final {best} model (n={len(full)}, through {full.index.max().date()}) -> {model_file}")

    with open(os.path.join(OUT, "rv_forecast_summary.md"), "w") as f:
        f.write(f"# RV forecast model vs GEX lookup baseline ({TODAY})\n\n")
        f.write(f"Rows: {len(full)} complete target rows {full.index.min().date()} .. {full.index.max().date()}; "
                f"walk-forward yearly refits 2018-2026, pooled OOS n={len(P)}.\n\n")
        f.write("Pooled metrics (log RV_fwd):\n\n```\n" + pooled[cols].round(4).to_string() + "\n```\n\n")
        f.write("Economic sign test:\n\n```\n" + pooled.loc[["baseline", "lookup_wf"] + names, ecols].round(3).to_string() + "\n```\n\n")
        f.write("Per-year RMSE:\n\n```\n" + py.round(4).to_string() + "\n```\n\n")
        f.write(f"Years beating baseline: {wins}\n\n")
        f.write("Ratio form:\n\n```\n" + ratio_df.round(4).to_string() + "\n```\n\n")
        f.write("har_struct coefficients (train through 2025):\n\n```\n" + coef.round(4).to_string() + "\n```\n\n")
        f.write("Sub-sample 2022-2026 intraday check:\n\n```\n" + sub_txt + "\n```\n\n")
        f.write(f"Best non-baseline: {best}. RMSE {m_rmse:.4f} vs baseline {b_rmse:.4f} ({100 * (m_rmse / b_rmse - 1):+.1f}%), "
                f"wins {wins[best]}/9, DM t {pooled.loc[best, 'dm_t_vs_baseline']:.2f}. "
                f"Econ Sharpe {pooled.loc[best, 'monthly_sharpe']:.3f} vs baseline {pooled.loc['baseline', 'monthly_sharpe']:.3f}.\n\n")
        f.write(f"Condition 1 (RMSE): {c1}. Condition 2 (econ): {c2}.\n\n## VERDICT: {verdict}\n\n")
        if model_file:
            f.write(f"Final model saved: {model_file}\n")
        else:
            f.write("No model saved; baseline lookup stands.\n")
    print("wrote results, pooled, predictions, summary")


if __name__ == "__main__":
    main()
