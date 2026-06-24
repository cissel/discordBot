#!/usr/bin/env python3
"""
predictSpy.py
=============
Loads the best SPY models and generates today's signal.

Output (JSON to stdout):
{
  "date": "2026-06-16",
  "next_day": {
    "direction": "UP" | "DOWN",
    "probability": 0.547,        # model confidence (0.5 = coin flip)
    "confidence": "LOW"|"MED"|"HIGH",
    "model": "logistic"
  },
  "next_5d": {
    "direction": "UP" | "DOWN",
    "expected_ret": 0.0082,       # predicted 5-day return
    "probability": 0.612,
    "confidence": "LOW"|"MED"|"HIGH",
    "model": "gbm"
  },
  "macro_context": {
    "vix": 18.4,
    "vix_regime": "NORMAL",       # LOW/<15, NORMAL/15-20, ELEVATED/20-30, FEAR/>30
    "yield_curve": -0.12,
    "fedfunds": 4.25,
    "fomc_in_days": 12,
    "cpi_in_days": 3,
    "nfp_in_days": 18,
    "is_event_window": false
  },
  "top_signals": ["vix_z252 bearish", "gld_spy_corr_21 risk-off", ...],
  "warnings": []
}

Usage:
  venv/bin/python3 python/predictSpy.py
  venv/bin/python3 python/predictSpy.py --date 2026-06-16   # specific date
"""

import os
import sys
import json
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

FEAT_PATH  = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR  = os.path.expanduser("~/discordBot/models/markets/spy")
CACHE_DIR  = os.path.expanduser("~/discordBot/outputs/markets/cache")

# Confidence thresholds on probability distance from 0.5
CONF_MED  = 0.03   # >53% or <47%
CONF_HIGH = 0.06   # >56% or <44%

VIX_REGIMES = [(15, "LOW"), (20, "NORMAL"), (30, "ELEVATED"), (999, "FEAR")]


def load_best_model(model_type, target):
    """Load the most recently saved model of a given type/target."""
    pattern = f"spy_{model_type}_{target}_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")],
        reverse=True
    )
    if not candidates:
        return None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle.get("features", [])


def load_regime_model(regime_name):
    """Load a regime-specific logistic model. Returns (model, feats) or (None, None).
    For 'bear': prefers the calibrated version if available."""
    if regime_name == "bear":
        cal_pattern = "spy_logistic_regime_bear_calibrated_"
        cal_candidates = sorted(
            [f for f in os.listdir(MODEL_DIR) if f.startswith(cal_pattern) and f.endswith(".pkl")],
            reverse=True
        )
        if cal_candidates:
            path = os.path.join(MODEL_DIR, cal_candidates[0])
            with open(path, "rb") as f:
                bundle = pickle.load(f)
            return bundle["model"], bundle.get("features", [])
    pattern = f"spy_logistic_regime_{regime_name}_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")
         and "calibrated" not in f],
        reverse=True
    )
    if not candidates:
        return None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle.get("features", [])


def predict_tomorrow_regime(df, row_df):
    """
    Use the regime prediction model to forecast tomorrow's regime.
    Returns (predicted_regime, probabilities_dict) or (None, None) if model unavailable.
    """
    pattern = "spy_regime_pred_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")],
        reverse=True
    )
    if not candidates:
        return None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)

    model     = bundle["model"]
    features  = bundle.get("features", [])
    class_map = bundle.get("class_map")   # None for logistic, dict for GBM

    # Build X row
    X_row = pd.DataFrame(index=row_df.index)
    for f in features:
        if f in df.columns:
            X_row[f] = row_df[f].values
        else:
            X_row[f] = 0.0
    for col in X_row.columns:
        if X_row[col].isna().any():
            med = df[col].median() if col in df.columns else 0.0
            X_row[col] = X_row[col].fillna(med if pd.notna(med) else 0.0)

    try:
        pred_raw  = model.predict(X_row.values)[0]
        # Convert int back to string if GBM (class_map provided)
        if class_map is not None:
            inv_map  = {v: k for k, v in class_map.items()}
            pred_str = inv_map.get(int(pred_raw), str(pred_raw))
        else:
            pred_str = str(pred_raw)

        # Probabilities
        probs_raw = model.predict_proba(X_row.values)[0]
        classes   = bundle.get("classes", ["bear", "bull", "chop"])
        probs     = {cls: round(float(p), 3) for cls, p in zip(classes, probs_raw)}
        return pred_str, probs
    except Exception:
        return None, None


def confidence_label(prob):
    dist = abs(prob - 0.5)
    if dist >= CONF_HIGH:
        return "HIGH"
    elif dist >= CONF_MED:
        return "MED"
    return "LOW"


def vix_regime(vix_val):
    for threshold, label in VIX_REGIMES:
        if vix_val < threshold:
            return label
    return "FEAR"


def days_to_event(date, event_dates):
    """Calendar days until next event on or after date."""
    evts = sorted(pd.to_datetime(event_dates))
    future = [e for e in evts if e.date() >= date]
    if not future:
        return 99
    return (future[0].date() - date).days


def get_top_signals(row, features):
    """Extract human-readable top signals from the feature row."""
    signals = []
    checks = [
        ("vix_z252",       lambda v: "VIX historically elevated" if v > 1.5 else ("VIX historically low" if v < -1.0 else None)),
        ("vix_z21",        lambda v: "VIX spiking short-term" if v > 2.0 else ("VIX compressing" if v < -1.5 else None)),
        ("spy_ret_r5",     lambda v: f"SPY up {v*100:.1f}% past week" if v > 0.02 else (f"SPY down {abs(v)*100:.1f}% past week" if v < -0.02 else None)),
        ("spy_ret_r63",    lambda v: f"SPY up {v*100:.0f}% past qtr" if v > 0.08 else (f"SPY down {abs(v)*100:.0f}% past qtr" if v < -0.05 else None)),
        ("spy_drawdown_252", lambda v: f"SPY {abs(v)*100:.0f}% off 52w high" if v < -0.08 else None),
        ("gld_spy_corr_21", lambda v: "Gold/SPY correlation positive (risk-off)" if v > 0.3 else ("Gold/SPY correlation negative (risk-on)" if v < -0.2 else None)),
        ("yield_curve",    lambda v: "Yield curve inverted" if v < 0 else (f"Yield curve steep +{v:.2f}pp" if v > 0.5 else None)),
        ("sector_rotation_r5", lambda v: "Risk-on sector leadership" if v > 0.002 else ("Risk-off sector leadership" if v < -0.002 else None)),
        ("spy_rsi_14",     lambda v: "RSI overbought (>70)" if v > 70 else ("RSI oversold (<30)" if v < 30 else None)),
        ("vix_rv_ratio",   lambda v: "VIX premium high (expensive options)" if v > 1.3 else ("VIX discount (cheap options)" if v < 0.8 else None)),
    ]
    for col, fn in checks:
        if col in row.index and pd.notna(row[col]):
            sig = fn(float(row[col]))
            if sig:
                signals.append(sig)
    return signals[:5]


def predict(target_date=None):
    # Load features
    if not os.path.exists(FEAT_PATH):
        return {"error": f"Feature file not found: {FEAT_PATH}. Run buildSpyFeatures.py first."}

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df = df.sort_values("date")

    if target_date:
        row_df = df[df["date"].dt.date == target_date]
        if row_df.empty:
            # Use last available row and note the date difference
            row_df = df.tail(1)
    else:
        row_df = df.tail(1)

    row      = row_df.iloc[-1]
    row_date = row["date"].date()
    warnings_list = []

    if target_date and row_date != target_date:
        warnings_list.append(f"No data for {target_date}, using most recent: {row_date}")

    # ── detect current regime ─────────────────────────────────────────────────
    # Regime is pre-computed in spy_features.csv (bull/bear/chop).
    # Used to (a) route to a regime-specific model, (b) add context to output.
    current_regime = str(row["regime"]) if "regime" in row.index and pd.notna(row.get("regime")) else None

    # ── predict tomorrow's regime (informational only) ────────────────────────
    # NOTE: The regime pred model scores 81.4% but the persistence baseline
    # (tomorrow == today) scores 89.5% — meaning the model actively degrades
    # routing accuracy. We keep it for the probability output in the embed
    # (useful to know P(bear) is rising) but route on TODAY's regime, not predicted.
    pred_regime, regime_probs = predict_tomorrow_regime(df, row_df)
    routing_regime = current_regime   # route on current, not predicted

    # ── 1-day direction model ─────────────────────────────────────────────────
    # Use regime-specific Logistic for TODAY's regime.
    # Fallback: universal Logistic if regime model unavailable.
    regime_model, regime_feats = (load_regime_model(routing_regime)
                                  if routing_regime else (None, None))
    if regime_model and regime_feats:
        logistic_model  = regime_model
        logistic_feats  = regime_feats
        model_label_1d  = f"logistic_regime_{routing_regime}"
    else:
        logistic_model, logistic_feats = load_best_model("logistic", "next_dir_1d")
        model_label_1d = "logistic"
        if routing_regime and not regime_model:
            warnings_list.append(f"Regime model '{routing_regime}' not found, using universal logistic")
    next_day_result = None
    if logistic_model and logistic_feats:
        avail = [f for f in logistic_feats if f in df.columns]
        missing = [f for f in logistic_feats if f not in df.columns]
        if missing:
            warnings_list.append(f"Logistic: {len(missing)} features missing from data (will impute with 0)")
        # Build X with exactly the features the model was trained on
        X_row = pd.DataFrame(index=row_df.index)
        for f in logistic_feats:
            if f in df.columns:
                X_row[f] = row_df[f].values
            else:
                X_row[f] = 0.0  # missing feature — impute with 0
        # Impute NaNs with column median from full history
        for col in X_row.columns:
            if X_row[col].isna().any():
                med = df[col].median() if col in df.columns else 0.0
                X_row[col] = X_row[col].fillna(med if pd.notna(med) else 0.0)
        try:
            prob_up = float(logistic_model.predict_proba(X_row.values)[0][1])
            direction = "UP" if prob_up >= 0.5 else "DOWN"
            next_day_result = {
                "direction":   direction,
                "probability": round(prob_up, 4),
                "confidence":  confidence_label(prob_up),
                "model":       model_label_1d,
                "regime":      current_regime,
                "regime_pred": routing_regime,
            }
        except Exception as e:
            warnings_list.append(f"Logistic inference error: {e}")
    else:
        warnings_list.append("Logistic model not found")

    # ── 5-day return model (GBM) ──────────────────────────────────────────────
    gbm_model, gbm_feats = load_best_model("gbm", "next_ret_5d")
    next_5d_result = None
    if gbm_model and gbm_feats:
        avail5 = [f for f in gbm_feats if f in df.columns]
        missing5 = [f for f in gbm_feats if f not in df.columns]
        if missing5:
            warnings_list.append(f"5d GBM: {len(missing5)} features missing (will impute with 0)")
        X_row5 = pd.DataFrame(index=row_df.index)
        for f in gbm_feats:
            if f in df.columns:
                X_row5[f] = row_df[f].values
            else:
                X_row5[f] = 0.0
        for col in X_row5.columns:
            if X_row5[col].isna().any():
                med = df[col].median() if col in df.columns else 0.0
                X_row5[col] = X_row5[col].fillna(med if pd.notna(med) else 0.0)
        try:
            pred_ret  = float(gbm_model.predict(X_row5.values)[0])
            direction5 = "UP" if pred_ret >= 0 else "DOWN"
            # Convert return to rough probability using historical distribution
            hist_std  = df["next_ret_5d"].std()
            z_score   = pred_ret / hist_std if hist_std > 0 else 0
            from scipy.stats import norm
            prob_up5  = float(norm.cdf(z_score))
            next_5d_result = {
                "direction":    direction5,
                "expected_ret": round(pred_ret, 5),
                "expected_ret_pct": round(pred_ret * 100, 2),
                "probability":  round(prob_up5, 4),
                "confidence":   confidence_label(prob_up5),
                "model":        "gbm",
            }
        except Exception as e:
            warnings_list.append(f"5d GBM inference error: {e}")
    else:
        warnings_list.append("5d GBM model not found")

    # ── macro context ─────────────────────────────────────────────────────────
    vix_val   = float(row["vix_level"])  if "vix_level"   in row.index and pd.notna(row["vix_level"])   else None
    yc_val    = float(row["yield_curve"]) if "yield_curve" in row.index and pd.notna(row["yield_curve"]) else None
    ff_val    = float(row["fedfunds"])    if "fedfunds"    in row.index and pd.notna(row["fedfunds"])    else None

    # Load event calendars for days-to
    try:
        import importlib.util
        cal_path = os.path.expanduser("~/discordBot/python/macro_event_calendars.py")
        spec = importlib.util.spec_from_file_location("macro_event_calendars", cal_path)
        cal  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cal)
        fomc_in = days_to_event(row_date, cal.FOMC_DATES)
        cpi_in  = days_to_event(row_date, cal.CPI_DATES)
        nfp_in  = days_to_event(row_date, cal.NFP_DATES)
        is_event_window = min(fomc_in, cpi_in, nfp_in) <= 1
    except Exception:
        fomc_in = cpi_in = nfp_in = None
        is_event_window = False

    macro_context = {
        "vix":             round(vix_val, 2) if vix_val is not None else None,
        "vix_regime":      vix_regime(vix_val) if vix_val is not None else None,
        "yield_curve":     round(yc_val, 3) if yc_val is not None else None,
        "fedfunds":        round(ff_val, 2) if ff_val is not None else None,
        "fomc_in_days":    fomc_in,
        "cpi_in_days":     cpi_in,
        "nfp_in_days":     nfp_in,
        "is_event_window": is_event_window,
    }

    top_signals = get_top_signals(row, logistic_feats or [])

    # ── position sizing (live rule, mirrors backtest best combo) ──────────────
    # Rule: half-bear t=0.53 + kill switch (flat when bear+GEX-+VIX spike)
    #   - Kill switch: regime=bear AND gex_sign<=0 AND vix_z21>1.5 -> 0.0
    #   - Bear regime otherwise: 0.5 (half size)
    #   - prob_up < 0.53: 0.0 (no signal)
    #   - Otherwise: 1.0 (full size)
    position_size   = 0.0
    sizing_reason   = "no signal (prob < 0.53)"
    if next_day_result:
        prob_up_live = next_day_result["probability"]
        gex_sign_live = float(row["gex_sign"]) if "gex_sign" in row.index and pd.notna(row.get("gex_sign")) else 1.0
        vix_z21_live  = float(row["vix_z21"])  if "vix_z21"  in row.index and pd.notna(row.get("vix_z21"))  else 0.0
        kill = (current_regime == "bear") and (gex_sign_live <= 0) and (vix_z21_live > 1.5)
        if kill:
            position_size = 0.0
            sizing_reason = f"kill switch (bear + GEX negative + VIX z21={vix_z21_live:.2f})"
        elif prob_up_live < 0.53:
            position_size = 0.0
            sizing_reason = f"no signal (prob={prob_up_live:.3f} < 0.53)"
        elif current_regime == "bear":
            position_size = 0.5
            sizing_reason = f"half size (bear regime, prob={prob_up_live:.3f})"
        else:
            position_size = 1.0
            sizing_reason = f"full size ({current_regime} regime, prob={prob_up_live:.3f})"

    result = {
        "date":          str(row_date),
        "next_day":      next_day_result,
        "next_5d":       next_5d_result,
        "macro_context": macro_context,
        "top_signals":   top_signals,
        "regime_probs":  regime_probs,   # P(bear/bull/chop) for tomorrow
        "position_size": position_size,
        "sizing_reason": sizing_reason,
        "warnings":      warnings_list,
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    target = datetime.date.fromisoformat(args.date) if args.date else None
    result = predict(target)
    print(json.dumps(result, indent=2))
