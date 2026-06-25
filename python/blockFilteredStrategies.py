"""
blockFilteredStrategies.py
--------------------------
Vectorized backtest. Tests 4 signal filters on the bull-block gap-fill strategy
and all 15 filter combinations.

Filters:
  A: DIX >= median on signal day (dark pool accumulation)
  B: GEX > 0 on signal day (market maker pinning regime)
  C: VIX term contango (vix9d <= vix3m) on signal day
  D: CVD normalized > 0 on signal day (order flow confirms)

Entry: T+1 open. Exit: gap fills (high >= block_price) or 1mo stop.
TC: 0.01% per side. No shorting. Cash when flat.
"""

import pandas as pd
import numpy as np
from itertools import combinations
from pathlib import Path

BASE = Path("/home/jhcv/discordBot")
OUT  = BASE / "outputs/research"
TC   = 0.0001

# ── load ───────────────────────────────────────────────────────────────────
blk  = pd.read_csv(OUT / "block_events.csv",   parse_dates=["trade_date"])
out  = pd.read_csv(OUT / "block_outcomes.csv",  parse_dates=["trade_date"])
gex  = pd.read_csv(BASE / "outputs/markets/cache/spy_gex_daily.csv",     parse_dates=["date"])
vix  = pd.read_csv(BASE / "outputs/markets/cache/vix_term_history.csv",  parse_dates=["date"])
cvd  = pd.read_csv(BASE / "outputs/features/markets/spy_orderflow_features.csv", parse_dates=["date"])
vwap = pd.read_csv(BASE / "outputs/markets/cache/spy_vwap_daily.csv",    parse_dates=["date"])

# ── merge events ───────────────────────────────────────────────────────────
events = blk.merge(
    out, on=["ticker","trade_date","size","deviation","dollar_value","exchange"], how="inner"
)
events = events[events["direction"] == "above_market"].copy()
print(f"Bull blocks: {len(events)}")

# ── attach filters ─────────────────────────────────────────────────────────
gx = gex.rename(columns={"date":"trade_date"})[["trade_date","dix","gex"]]
vt = vix.rename(columns={"date":"trade_date"})[["trade_date","vix9d","vix3m"]]
cv = cvd.rename(columns={"date":"trade_date"})[["trade_date","cvd_normalized"]]
vw = vwap.rename(columns={"date":"trade_date"})[["trade_date","open_price","close_price"]]

events = events.merge(gx, on="trade_date", how="left")
events = events.merge(vt, on="trade_date", how="left")
events = events.merge(cv, on="trade_date", how="left")
events = events.merge(vw, on="trade_date", how="left")

dix_med = events["dix"].median()

events["filt_A"] = events["dix"] >= dix_med
events["filt_B"] = events["gex"] > 0
events["filt_C"] = (events["vix9d"] <= events["vix3m"]) & events["vix9d"].notna()
events["filt_D"] = events["cvd_normalized"] > 0

print("\nFilter pass rates:")
for f, col in [("A DIX>=med","filt_A"),("B GEX>0","filt_B"),
               ("C VIX contango","filt_C"),("D CVD>0","filt_D")]:
    n = events[col].sum()
    print(f"  {f}: {n}/{len(events)} ({100*n/len(events):.1f}%)")

# ── SPY price series ───────────────────────────────────────────────────────
vw_idx = vwap.set_index("date").sort_index()
spy_open  = vw_idx["open_price"]
spy_close = vw_idx["close_price"]
spy_high  = vw_idx[["open_price","close_price"]].max(axis=1)  # approx daily high
all_dates = spy_open.index

# ── build trade list for a given event subset ──────────────────────────────
def build_trades(ev_subset):
    rows = []
    open_arr  = spy_open.values
    close_arr = spy_close.values
    high_arr  = spy_high.values
    dates_arr = all_dates

    for _, row in ev_subset.iterrows():
        block_px    = row["block_price"]
        signal_date = row["trade_date"]

        # T+1 entry
        future_idx = np.searchsorted(dates_arr, signal_date, side="right")
        if future_idx >= len(dates_arr):
            continue
        entry_date = dates_arr[future_idx]
        entry_px   = open_arr[future_idx]

        # scan up to 22 bars for gap fill (1mo)
        filled = False
        exit_date = None
        exit_px   = None
        end_idx   = min(future_idx + 22, len(dates_arr))

        for i in range(future_idx, end_idx):
            if high_arr[i] >= block_px:
                exit_date = dates_arr[i]
                exit_px   = block_px
                filled    = True
                break

        if not filled:
            i = end_idx - 1
            exit_date = dates_arr[i]
            exit_px   = close_arr[i]

        ret = (exit_px / entry_px - 1) - 2 * TC
        rows.append({
            "entry_date": entry_date,
            "exit_date":  exit_date,
            "entry_px":   entry_px,
            "exit_px":    exit_px,
            "filled":     filled,
            "ret":        ret,
            "block_px":   block_px,
        })
    return pd.DataFrame(rows)


# ── vectorized equity curve ────────────────────────────────────────────────
def equity_curve(trades_df):
    """
    For each calendar day: if ANY trade is open, SPY daily return applied.
    Multiple simultaneous trades are treated as equal-weight slices of 100% invested.
    Cash when no trade open. TC deducted at entry and exit dates.
    """
    if trades_df.empty:
        return pd.Series(1.0, index=all_dates)

    close_arr  = spy_close.values
    dates_arr  = all_dates
    n          = len(dates_arr)
    port       = np.ones(n)

    # count active trades per day (vectorized via boolean arrays)
    entry_idxs = np.searchsorted(dates_arr, trades_df["entry_date"].values)
    exit_idxs  = np.searchsorted(dates_arr, trades_df["exit_date"].values, side="right")

    active_count = np.zeros(n, dtype=int)
    for ei, ex in zip(entry_idxs, exit_idxs):
        active_count[ei:ex] += 1

    # daily SPY returns
    spy_ret = np.zeros(n)
    spy_ret[1:] = close_arr[1:] / np.where(close_arr[:-1] == 0, np.nan, close_arr[:-1]) - 1
    spy_ret = np.nan_to_num(spy_ret, nan=0.0)

    # portfolio return: 100% in SPY when any trade open, else 0%
    invested = (active_count > 0).astype(float)
    port_ret = invested * spy_ret

    # compound
    port = np.cumprod(1 + port_ret)

    # apply TC at entry/exit days (each trade = 2x TC)
    tc_mult = np.ones(n)
    for ei, ex in zip(entry_idxs, exit_idxs):
        if ei < n:
            tc_mult[ei] *= (1 - TC)
        if ex - 1 < n:
            tc_mult[ex - 1] *= (1 - TC)

    port = port * np.cumprod(tc_mult)

    return pd.Series(port, index=all_dates)


# ── metrics ────────────────────────────────────────────────────────────────
def metrics(eq, trades_df, label):
    eq = eq.dropna()
    n_years  = len(eq) / 252
    total    = eq.iloc[-1] / eq.iloc[0] - 1
    cagr     = (eq.iloc[-1] / eq.iloc[0]) ** (1/max(n_years, 0.01)) - 1
    dr       = eq.pct_change().dropna()
    sharpe   = dr.mean() / dr.std() * np.sqrt(252) if dr.std() > 0 else 0
    mdd      = ((eq - eq.cummax()) / eq.cummax()).min()
    calmar   = cagr / abs(mdd) if mdd != 0 else 0
    n        = len(trades_df)
    wr       = (trades_df["ret"] > 0).mean() * 100 if n > 0 else 0
    fr       = trades_df["filled"].mean() * 100 if n > 0 else 0
    return dict(label=label, total_ret=round(total*100,2), cagr=round(cagr*100,2),
                sharpe=round(sharpe,3), max_dd=round(mdd*100,2), calmar=round(calmar,3),
                n_trades=n, win_rate=round(wr,1), fill_rate=round(fr,1))


# ── run all combos ─────────────────────────────────────────────────────────
filter_map = {"A":"filt_A","B":"filt_B","C":"filt_C","D":"filt_D"}
filter_labels = {
    "A": "DIX>=med",
    "B": "GEX>0",
    "C": "VIX contango",
    "D": "CVD>0",
}

all_equities = {}
all_metrics  = []

# BnH
bnh = spy_close / spy_close.iloc[0]
bnh_dr = bnh.pct_change().dropna()
ny = len(bnh)/252
all_equities["BnH"] = bnh
all_metrics.append(dict(
    label="BnH SPY",
    total_ret=round((bnh.iloc[-1]-1)*100,2),
    cagr=round(((bnh.iloc[-1])**(1/ny)-1)*100,2),
    sharpe=round(bnh_dr.mean()/bnh_dr.std()*np.sqrt(252),3),
    max_dd=round(((bnh-bnh.cummax())/bnh.cummax()).min()*100,2),
    calmar=round(((bnh.iloc[-1])**(1/ny)-1)/abs(((bnh-bnh.cummax())/bnh.cummax()).min()),3),
    n_trades=1, win_rate=100.0, fill_rate=100.0
))

# Baseline
print("\nBaseline...")
base_trades = build_trades(events)
base_eq     = equity_curve(base_trades)
all_equities["Baseline"] = base_eq
all_metrics.append(metrics(base_eq, base_trades, "Baseline (no filter)"))

# Single filters
for k in ["A","B","C","D"]:
    mask = events[filter_map[k]].fillna(False)
    ev_sub = events[mask]
    print(f"Filter {k}: {len(ev_sub)} trades...")
    trd = build_trades(ev_sub)
    eq  = equity_curve(trd)
    lbl = f"Filter {k}: {filter_labels[k]}"
    all_equities[f"F{k}"] = eq
    all_metrics.append(metrics(eq, trd, lbl))

# All 2,3,4-way combos
for r in range(2, 5):
    for combo in combinations(["A","B","C","D"], r):
        mask = pd.Series(True, index=events.index)
        for k in combo:
            mask = mask & events[filter_map[k]].fillna(False)
        ev_sub = events[mask]
        key = "+".join(combo)
        lbl = " & ".join(filter_labels[k] for k in combo)
        print(f"Combo {key}: {len(ev_sub)} trades...")
        trd = build_trades(ev_sub)
        eq  = equity_curve(trd)
        all_equities[f"C_{key}"] = eq
        all_metrics.append(metrics(eq, trd, f"{key}: {lbl}"))

# ── save ───────────────────────────────────────────────────────────────────
summary_df = pd.DataFrame(all_metrics)
summary_df.to_csv(OUT / "filtered_summary.csv", index=False)

equity_df = pd.DataFrame(all_equities)
equity_df.index.name = "date"
equity_df.to_csv(OUT / "filtered_equity.csv")

print("\n=== RESULTS ===")
print(summary_df[["label","total_ret","cagr","sharpe","max_dd","calmar","n_trades","win_rate"]].to_string(index=False))
print(f"\nDone. Saved filtered_equity.csv + filtered_summary.csv")
