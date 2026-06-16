#!/usr/bin/env python3
"""
predictBtc.py
=============
Loads the latest BTC model pkl bundles and outputs a JSON signal.

Outputs JSON to stdout with:
  - next-day direction + probability
  - 5-day return magnitude + direction
  - on-chain context (MVRV, NUPL, hashrate growth, halving cycle)
  - macro context (fedfunds, yield curve, DXY)
  - top signals narrative

Usage:
  venv/bin/python3 python/predictBtc.py
"""

import os
import sys
import glob
import json
import pickle
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE      = os.path.expanduser("~/discordBot")
FEAT_PATH = os.path.join(BASE, "outputs/features/markets/btc_features.csv")
MODEL_DIR = os.path.join(BASE, "models/markets/btc")

SPARSE_FEATURES = [
    "btc_mvrv", "btc_nupl", "btc_mvrv_z63", "btc_mvrv_z252", "btc_mvrv_chg_21",
    "btc_realized_mvrv", "btc_realized_mvrv_chg_21",
    "btc_blkcnt_ratio", "btc_blkcnt_z21",
    "btc_dominance", "btc_dominance_chg_21", "btc_dominance_z63",
    "btc_s2f_dev", "btc_s2f_dev_z63",
    "eth_ret_1d", "eth_ret_5d", "btc_eth_rs_5d",
    "spy_ret_1d", "spy_ret_5d", "spy_z21",
    "gld_ret_1d", "gld_ret_5d", "gld_z21",
    "uso_ret_1d", "uso_z21",
    "btc_gld_corr_21",
]


def load_best_model(model_type, target):
    pattern = os.path.join(MODEL_DIR, f"btc_{model_type}_{target}_*.pkl")
    files   = sorted(glob.glob(pattern))
    if not files:
        return None, []
    with open(files[-1], "rb") as fh:
        bundle = pickle.load(fh)
    return bundle["model"], bundle.get("features", [])


def build_row(df, feat_list):
    """Build a single-row feature matrix from the last available row."""
    row = df.tail(1).copy()
    for col in feat_list:
        if col not in row.columns:
            row[col] = 0.0
    # Impute sparse features with column median from full df
    for col in SPARSE_FEATURES:
        if col in feat_list and pd.isna(row[col].values[0]):
            med = df[col].median() if col in df.columns else 0.0
            row[col] = med
    return row[feat_list].fillna(0.0)


def confidence_label(prob):
    if prob > 0.58 or prob < 0.42:
        return "HIGH"
    elif prob > 0.54 or prob < 0.46:
        return "MED"
    return "LOW"


def main():
    if not os.path.exists(FEAT_PATH):
        print(json.dumps({"error": "btc_features.csv not found. Run buildBtcFeatures.py first."}))
        sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if len(df) == 0:
        print(json.dumps({"error": "Empty feature file"}))
        sys.exit(1)

    last = df.iloc[-1]
    date_str = str(last["date"].date()) if hasattr(last["date"], "date") else str(last["date"])[:10]

    # ── Load models ────────────────────────────────────────────────────────────
    logistic_model, logistic_feats = load_best_model("logistic", "next_dir_1d")
    gbm5d_model,    gbm5d_feats    = load_best_model("gbm", "next_ret_5d")

    result = {"date": date_str}

    # ── 1d direction ───────────────────────────────────────────────────────────
    if logistic_model is not None:
        X_row = build_row(df, logistic_feats)
        prob  = float(logistic_model.predict_proba(X_row)[0, 1])
        direction = "UP" if prob > 0.5 else "DOWN"
        result["next_day"] = {
            "direction":   direction,
            "probability": round(prob, 3),
            "confidence":  confidence_label(prob),
            "model":       "logistic",
        }
    else:
        result["next_day"] = {"error": "No logistic model trained yet"}

    # ── 5d return ─────────────────────────────────────────────────────────────
    if gbm5d_model is not None:
        X_row       = build_row(df, gbm5d_feats)
        expected_ret = float(gbm5d_model.predict(X_row)[0])
        prob_5d     = float(np.clip((expected_ret + 0.1) / 0.2, 0.0, 1.0))  # rough prob from return
        direction5  = "UP" if expected_ret > 0 else "DOWN"
        result["next_5d"] = {
            "direction":        direction5,
            "expected_ret":     round(expected_ret, 4),
            "expected_ret_pct": round(expected_ret * 100, 2),
            "confidence":       confidence_label(0.5 + abs(expected_ret) * 5),
            "model":            "gbm",
        }
    else:
        result["next_5d"] = {"error": "No 5d GBM model trained yet"}

    # ── On-chain context ───────────────────────────────────────────────────────
    onchain = {}
    for col, label in [
        ("btc_mvrv",              "mvrv"),
        ("btc_nupl",              "nupl"),
        ("btc_hashrate_growth_21","hashrate_growth_21d"),
        ("btc_halving_cycle_pct", "halving_cycle_pct"),
        ("btc_dominance",         "btc_dominance"),
        ("btc_vol_regime",        "vol_regime"),
    ]:
        val = last.get(col, np.nan)
        onchain[label] = round(float(val), 4) if pd.notna(val) else None

    # Halving cycle label
    cycle_pct = last.get("btc_halving_cycle_pct", np.nan)
    if pd.notna(cycle_pct):
        if cycle_pct < 0.25:
            onchain["cycle_phase"] = "Post-Halving Accumulation"
        elif cycle_pct < 0.50:
            onchain["cycle_phase"] = "Bull Run Phase"
        elif cycle_pct < 0.75:
            onchain["cycle_phase"] = "Distribution Phase"
        else:
            onchain["cycle_phase"] = "Pre-Halving Run-Up"

    # MVRV zone
    mvrv_val = last.get("btc_mvrv", np.nan)
    if pd.notna(mvrv_val):
        if mvrv_val < 1:
            onchain["mvrv_zone"] = "Undervalued"
        elif mvrv_val < 2:
            onchain["mvrv_zone"] = "Fair Value"
        elif mvrv_val < 3.5:
            onchain["mvrv_zone"] = "Overvalued"
        else:
            onchain["mvrv_zone"] = "Euphoria"

    result["onchain_context"] = onchain

    # ── Macro context ──────────────────────────────────────────────────────────
    macro = {}
    for col, label in [
        ("fedfunds",      "fedfunds"),
        ("yield_curve",   "yield_curve"),
        ("dxy_level",     "dxy_level"),
        ("btc_rv_21",     "realized_vol_21d"),
    ]:
        val = last.get(col, np.nan)
        macro[label] = round(float(val), 3) if pd.notna(val) else None
    result["macro_context"] = macro

    # ── Top signals narrative ──────────────────────────────────────────────────
    signals = []

    ret5 = last.get("btc_ret_r5", np.nan)
    if pd.notna(ret5):
        pct5 = ret5 * 100
        if pct5 > 5:
            signals.append(f"BTC up {pct5:.1f}% past week (momentum)")
        elif pct5 < -5:
            signals.append(f"BTC down {abs(pct5):.1f}% past week (sell pressure)")

    if pd.notna(mvrv_val):
        if mvrv_val > 3.5:
            signals.append(f"MVRV {mvrv_val:.2f} - euphoria zone (historically peaks near 4-5)")
        elif mvrv_val < 1:
            signals.append(f"MVRV {mvrv_val:.2f} - below realized price (accumulation zone)")
        else:
            signals.append(f"MVRV {mvrv_val:.2f} - fair value range")

    nupl_val = last.get("btc_nupl", np.nan)
    if pd.notna(nupl_val):
        if nupl_val > 0.5:
            signals.append(f"NUPL {nupl_val:.2f} - euphoria/greed territory")
        elif nupl_val < 0:
            signals.append(f"NUPL {nupl_val:.2f} - fear/capitulation territory")

    hgrow = last.get("btc_hashrate_growth_21", np.nan)
    if pd.notna(hgrow):
        if hgrow > 0.05:
            signals.append(f"Hashrate growing rapidly (+{hgrow:.2f} log, 21d) - miner confidence")
        elif hgrow < -0.05:
            signals.append(f"Hashrate declining ({hgrow:.2f} log, 21d) - potential miner stress")

    dom_chg = last.get("btc_dominance_chg_21", np.nan)
    if pd.notna(dom_chg):
        if dom_chg > 0.02:
            signals.append("BTC dominance rising (alt-season fading, BTC-centric)")
        elif dom_chg < -0.02:
            signals.append("BTC dominance falling (alt-season risk-on)")

    result["top_signals"] = signals[:5]  # cap at 5

    print(json.dumps(result))


if __name__ == "__main__":
    main()
