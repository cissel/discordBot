#!/usr/bin/env python3
"""
spyIntradaySwingWfcvGated.py
==============================
ONE pre-registered side test of the intraday swing-reversal strategy with a
vol-regime entry gate. Identical to spyIntradaySwingWfcvFull.py (4-fold
expanding-window WFCV, same model, same thresholds, same costs) EXCEPT that
on INELIGIBLE days the strategy takes no positions at all.

PRE-REGISTERED GATE (fixed before running, not tuned):
  A trading day is ELIGIBLE iff
    (1) the prior trading day's VIX close < 25        (VIX.csv: date,VIXCLS_val)
    (2) the day is NOT in GEX trailing-252 quintile 1 (most negative GEX),
        where the quintile is the rank of a day's gex within the trailing
        252 values, lagged one day so only information known before the
        trading day is used (spy_gex_daily.csv: date,price,dix,gex).
  Days where either input is missing are treated as INELIGIBLE (no trade).

Implementation: prob_high / prob_low are zeroed on ineligible bars before
calling m.backtest_reversal. Since positions are forced flat at every day
boundary and exits only depend on the probs when a position is held, this is
exactly equivalent to taking no positions on those days. Model training is
untouched (trains on all days). Daily P&L on ineligible days is 0 and those
days are KEPT in the daily series, so Sharpe is measured over the same
calendar as the ungated run (capital idle on gated-out days).

PRE-REGISTERED PASS RULE: fold 1 Sharpe >= 0 AND pooled bootstrap 95% CI lower
bound > 0, at 0.12bp per side. Both required.

Usage:
  venv/bin/python3 python/spyIntradaySwingWfcvGated.py ""          # 0.15% labels
  venv/bin/python3 python/spyIntradaySwingWfcvGated.py _pct40bps
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

import spyIntradaySwingTrain as m
from spyVolTargetWFCV import run_wfcv

_SFX = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "_pct40bps"
SWING_LABEL_PATH = os.path.expanduser(
    f"~/discordBot/outputs/features/markets/spy_intraday_swing_labels{_SFX}.csv")
OUT_PATH = os.path.expanduser("~/discordBot/outputs/markets/spy_swing_wfcv_gated_daily_pnl.csv")
VIX_PATH = os.path.expanduser("~/discordBot/outputs/markets/cache/VIX.csv")
GEX_PATH = os.path.expanduser("~/discordBot/outputs/markets/cache/spy_gex_daily.csv")
SEED = 42

VIX_MAX = 25.0          # prior-day VIX close must be strictly below this
GEX_WINDOW = 252        # trailing window for GEX quintile
GEX_EXCLUDE_QUINTILE = 1


def ann_sharpe(r):
    r = np.asarray(r)
    if len(r) == 0 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(252))


def block_bootstrap(r, block=21, n_boot=5000, seed=SEED):
    rng = np.random.default_rng(seed)
    n = len(r)
    if n <= block:
        block = max(1, n - 1)
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    out = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        resampled = np.concatenate([r[s:s + block] for s in starts])[:n]
        out[b] = ann_sharpe(resampled)
    return out


def build_eligibility(trading_dates):
    """Return a DataFrame indexed by trading date (datetime.date) with columns
    vix_prev, gex_q_prev, eligible, plus per-condition flags. Only data
    strictly before each trading day is used."""
    td = pd.DataFrame({"trading_date": pd.to_datetime(sorted(set(trading_dates)))})

    vix = pd.read_csv(VIX_PATH, parse_dates=["date"]).dropna(subset=["VIXCLS_val"])
    vix = vix.sort_values("date").rename(columns={"VIXCLS_val": "vix_prev"})
    # prior-day VIX close: most recent VIX date STRICTLY before the trading day
    td = pd.merge_asof(td, vix[["date", "vix_prev"]], left_on="trading_date",
                       right_on="date", direction="backward", allow_exact_matches=False)
    td = td.drop(columns=["date"])

    gex = pd.read_csv(GEX_PATH, parse_dates=["date"]).dropna(subset=["gex"]).sort_values("date")
    # rank of today's gex within trailing 252 values (inclusive of today), as a
    # percentile in (0,1]; quintile 1 = lowest 20%
    gex["gex_pct"] = gex["gex"].rolling(GEX_WINDOW, min_periods=GEX_WINDOW).apply(
        lambda w: (w <= w[-1]).mean(), raw=True)
    gex["gex_q"] = np.ceil(gex["gex_pct"] * 5).clip(1, 5)
    gex = gex.rename(columns={"gex_q": "gex_q_prev"})
    # lag one day: most recent GEX date STRICTLY before the trading day
    td = pd.merge_asof(td, gex[["date", "gex_q_prev"]], left_on="trading_date",
                       right_on="date", direction="backward", allow_exact_matches=False)
    td = td.drop(columns=["date"])

    td["vix_ok"] = td["vix_prev"] < VIX_MAX
    td["gex_ok"] = td["gex_q_prev"].notna() & (td["gex_q_prev"] != GEX_EXCLUDE_QUINTILE)
    td["eligible"] = td["vix_ok"] & td["gex_ok"]
    td["trading_date"] = td["trading_date"].dt.date
    return td.set_index("trading_date")


def fold_stats(daily_pnl):
    sharpe = ann_sharpe(daily_pnl.values)
    boot = block_bootstrap(daily_pnl.values)
    ci = np.percentile(boot, [2.5, 97.5])
    pct_pos = (boot > 0).mean() * 100
    return dict(sharpe=sharpe, total_ret=float(daily_pnl.sum()), boot_mean=float(boot.mean()),
                ci=ci, pct_pos=pct_pos)


def main():
    print(f"Loading 5-min features + swing labels (suffix='{_SFX}')...")
    feat = pd.read_csv(m.FEAT_PATH, parse_dates=["date"])
    feat["trading_date"] = pd.to_datetime(feat["trading_date"]).dt.date
    labels = pd.read_csv(SWING_LABEL_PATH, parse_dates=["date"])
    labels["trading_date"] = pd.to_datetime(labels["trading_date"]).dt.date
    df = feat.merge(labels[["trading_date", "bar_idx_in_day", "is_swing_high", "is_swing_low",
                             "swing_high_near", "swing_low_near"]],
                     on=["trading_date", "bar_idx_in_day"], how="inner")
    df = m.add_extra_causal_features(df)
    print(f"Merged: {len(df)} bars, {df['trading_date'].nunique()} days")
    print(f"TX cost per side: {m.TX_COST_BPS} bp")

    df_daily = pd.read_csv(os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv"),
                            parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos_daily = run_wfcv(df_daily)
    fold_date_map = {fid: set(oos_daily.loc[oos_daily["fold"] == fid, "date"].dt.date)
                     for fid in sorted(oos_daily["fold"].unique())}
    df["fold"] = 0
    for fid, dates in fold_date_map.items():
        df.loc[df["trading_date"].isin(dates), "fold"] = fid
    print("Fold day counts:", df.groupby("fold")["trading_date"].nunique().to_dict())

    elig = build_eligibility(df["trading_date"].unique())
    print(f"\nGATE: prior-day VIX < {VIX_MAX} AND prior-day GEX trailing-{GEX_WINDOW} quintile != {GEX_EXCLUDE_QUINTILE}")
    print(f"  Days with missing VIX: {elig['vix_prev'].isna().sum()}  "
          f"missing GEX quintile: {elig['gex_q_prev'].isna().sum()}  (missing => ineligible)")
    print(f"  Overall eligible: {elig['eligible'].mean()*100:.1f}% of {len(elig)} days "
          f"(VIX ok {elig['vix_ok'].mean()*100:.1f}%, GEX ok {elig['gex_ok'].mean()*100:.1f}%)")

    all_results = {}
    gated_daily, ungated_daily, elig_rows = [], [], []
    for eval_fold in [1, 2, 3, 4]:
        label = f"Fold {eval_fold}: expanding train (all days < fold {eval_fold} start)"
        print(f"\n{'='*70}\n{label}\n{'='*70}")
        df_train = df[df["fold"] < eval_fold].reset_index(drop=True)
        df_eval = df[df["fold"] == eval_fold].reset_index(drop=True)
        n_train_days = df_train["trading_date"].nunique()
        n_eval_days = df_eval["trading_date"].nunique()
        print(f"  Train bars: {len(df_train)} ({n_train_days} days)  "
              f"Eval bars: {len(df_eval)} ({n_eval_days} days)")
        if n_train_days < 60 or len(df_train) < 2000:
            print("  SKIPPED: insufficient training history")
            continue

        # model training identical to the ungated run (all days, no gate)
        model_high = m.train_classifier(df_train, "swing_high_near")
        model_low = m.train_classifier(df_train, "swing_low_near")
        X_eval = df_eval[m.ALL_FEATURES].fillna(0.0).values
        prob_high = model_high.predict_proba(X_eval)[:, 1]
        prob_low = model_low.predict_proba(X_eval)[:, 1]

        from sklearn.metrics import roc_auc_score
        auc_high = roc_auc_score(df_eval["swing_high_near"].values, prob_high)
        auc_low = roc_auc_score(df_eval["swing_low_near"].values, prob_low)
        print(f"  Eval AUC swing_high: {auc_high:.4f}  swing_low: {auc_low:.4f}")

        # eligibility mask per bar
        eval_elig = elig.reindex(df_eval["trading_date"].unique())
        bar_elig = df_eval["trading_date"].map(eval_elig["eligible"]).fillna(False).values.astype(bool)
        frac_elig = float(eval_elig["eligible"].mean())
        vix_frac = float(eval_elig["vix_ok"].mean())
        gex_frac = float(eval_elig["gex_ok"].mean())
        print(f"  Eligible days: {int(eval_elig['eligible'].sum())}/{n_eval_days} = {frac_elig*100:.1f}%  "
              f"(VIX<{VIX_MAX:.0f}: {vix_frac*100:.1f}%, GEX not Q1: {gex_frac*100:.1f}%)")

        # UNGATED (reference, same model/probs)
        tp_u, _, nt_u = m.backtest_reversal(df_eval, prob_high, prob_low)
        d_u = m.daily_pnl_from_bar_pnl(df_eval, tp_u)
        s_u = fold_stats(d_u)

        # GATED: zero probs on ineligible bars -> no entries on those days
        ph_g = np.where(bar_elig, prob_high, 0.0)
        pl_g = np.where(bar_elig, prob_low, 0.0)
        tp_g, _, nt_g = m.backtest_reversal(df_eval, ph_g, pl_g)
        d_g = m.daily_pnl_from_bar_pnl(df_eval, tp_g)
        s_g = fold_stats(d_g)
        # sanity: gated P&L must be exactly zero on ineligible days
        assert np.allclose(d_g.reindex(eval_elig.index[~eval_elig["eligible"]]).fillna(0).values, 0.0)

        for name, s, nt in [("UNGATED", s_u, nt_u), ("GATED  ", s_g, nt_g)]:
            print(f"  {name}: N trades={nt:5d}  Sharpe={s['sharpe']:+.3f}  Total P&L={s['total_ret']*100:+.2f}%  "
                  f"Boot mean={s['boot_mean']:+.3f}  95%CI=[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}]  "
                  f"P(>0)={s['pct_pos']:.1f}%")

        all_results[eval_fold] = dict(frac_elig=frac_elig, auc_high=auc_high, auc_low=auc_low,
                                       u=dict(n_trades=nt_u, **s_u), g=dict(n_trades=nt_g, **s_g))
        gated_daily.append(d_g)
        ungated_daily.append(d_u)
        ev = eval_elig.copy()
        ev["fold"] = eval_fold
        elig_rows.append(ev)

    print(f"\n{'='*70}\nSUMMARY: GATED vs UNGATED (cost {m.TX_COST_BPS}bp/side)\n{'='*70}")
    print(f"  {'Fold':<7}{'Elig%':>7}  {'Sharpe U':>9} {'Sharpe G':>9}  {'Trades U':>9} {'Trades G':>9}  "
          f"{'P&L U':>8} {'P&L G':>8}  {'CI U':>17} {'CI G':>17}  {'P>0 U':>6} {'P>0 G':>6}")
    for fid, r in all_results.items():
        u, g = r["u"], r["g"]
        print(f"  {fid:<7}{r['frac_elig']*100:>6.1f}%  {u['sharpe']:>+9.3f} {g['sharpe']:>+9.3f}  "
              f"{u['n_trades']:>9d} {g['n_trades']:>9d}  {u['total_ret']*100:>+7.2f}% {g['total_ret']*100:>+7.2f}%  "
              f"[{u['ci'][0]:+.2f},{u['ci'][1]:+.2f}]   [{g['ci'][0]:+.2f},{g['ci'][1]:+.2f}]   "
              f"{u['pct_pos']:>5.1f}% {g['pct_pos']:>5.1f}%")

    if gated_daily:
        pg = pd.concat(gated_daily)
        pg = pg[~pg.index.duplicated(keep="first")]
        pu = pd.concat(ungated_daily)
        pu = pu[~pu.index.duplicated(keep="first")]
        el = pd.concat(elig_rows)
        el = el[~el.index.duplicated(keep="first")]
        sg, su = fold_stats(pg), fold_stats(pu)
        tg = sum(r["g"]["n_trades"] for r in all_results.values())
        tu = sum(r["u"]["n_trades"] for r in all_results.values())
        print(f"\n  POOLED UNGATED ({len(pu)} days, {tu} trades): Sharpe={su['sharpe']:+.3f}  "
              f"Boot mean={su['boot_mean']:+.3f}  95%CI=[{su['ci'][0]:+.3f},{su['ci'][1]:+.3f}]  P(>0)={su['pct_pos']:.1f}%")
        print(f"  POOLED GATED   ({len(pg)} days, {tg} trades, {el['eligible'].mean()*100:.1f}% eligible): "
              f"Sharpe={sg['sharpe']:+.3f}  Boot mean={sg['boot_mean']:+.3f}  "
              f"95%CI=[{sg['ci'][0]:+.3f},{sg['ci'][1]:+.3f}]  P(>0)={sg['pct_pos']:.1f}%")

        # Also: Sharpe on eligible days only (informational, NOT the pass rule)
        pg_elig = pg[el.reindex(pg.index)["eligible"].fillna(False).values]
        print(f"  (info) GATED Sharpe over eligible days only ({len(pg_elig)} days): {ann_sharpe(pg_elig.values):+.3f}")

        f1 = all_results.get(1, {}).get("g", {}).get("sharpe", np.nan)
        c1 = bool(f1 >= 0)
        c2 = bool(sg["ci"][0] > 0)
        print(f"\n  PRE-REGISTERED PASS RULE: fold1 gated Sharpe >= 0 -> {f1:+.3f} {'OK' if c1 else 'FAIL'}; "
              f"pooled gated CI lower > 0 -> {sg['ci'][0]:+.3f} {'OK' if c2 else 'FAIL'}")
        print(f"  VERDICT: {'PASS' if (c1 and c2) else 'FAIL'}")

        out = pd.DataFrame({"gated_pnl": pg, "ungated_pnl": pu.reindex(pg.index)})
        out = out.join(el[["fold", "vix_prev", "gex_q_prev", "eligible"]])
        out.index.name = "trading_date"
        out.to_csv(OUT_PATH)
        print(f"\n  Saved daily P&L (gated + ungated + gate inputs) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
