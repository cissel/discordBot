#!/usr/bin/env python3
# optionsSnapshot.py - daily close-of-day options metrics snapshot for model assets
#
# Run at 4:15pm ET on trading days (after market close, before options settlement).
# Appends one row per asset per day to outputs/research/<ASSET>_options_daily.csv
#
# Metrics captured per asset:
#   date, spot, atm_iv_c, atm_iv_p, atm_iv_avg   - ATM implied vol (call, put, avg)
#   iv_skew_otm                                   - OTM put IV minus OTM call IV (~95/105% strikes)
#   iv_term_slope                                 - nearest expiry IV minus 2nd-nearest expiry IV
#   pcr_vol                                       - put/call volume ratio
#   pcr_oi                                        - put/call open interest ratio (if available)
#   total_call_vol, total_put_vol                 - total daily volume
#   nearest_exp, nearest_dte                      - front-month expiry info
#
# Usage:
#   python3 optionsSnapshot.py [TICKER [TICKER ...]]   (default: SPY)

import os
import sys
import re
import json
import requests
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

HDRS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

OUT_DIR = os.path.expanduser("~/discordBot/outputs/research/")
os.makedirs(OUT_DIR, exist_ok=True)

today     = date.today()
today_str = today.isoformat()

TICKERS = [t.upper() for t in sys.argv[1:]] if len(sys.argv) > 1 else ["SPY"]


def get_spot(ticker):
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
        headers=HDRS,
        params={"timeframe": "1Day", "limit": 1, "feed": "sip", "adjustment": "all"},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"spot fetch failed: {r.status_code}")
    bars = r.json().get("bars", [])
    if not bars:
        raise RuntimeError(f"no bars for {ticker}")
    return float(bars[0]["c"])


def get_options_chain(ticker, dte_max=60):
    """Paginate through options snapshots for the given ticker up to dte_max days out."""
    all_snaps = {}
    token = None
    exp_end = (today + timedelta(days=dte_max)).isoformat()

    while True:
        params = {
            "feed":                  "indicative",
            "limit":                 1000,
            "expiration_date_gte":   today_str,
            "expiration_date_lte":   exp_end,
        }
        if token:
            params["page_token"] = token

        r = requests.get(
            f"https://data.alpaca.markets/v1beta1/options/snapshots/{ticker}",
            headers=HDRS,
            params=params,
            timeout=20,
        )
        if r.status_code != 200:
            raise RuntimeError(f"options chain fetch failed: {r.status_code} {r.text[:200]}")

        data  = r.json()
        all_snaps.update(data.get("snapshots", {}))
        token = data.get("next_page_token")
        if not token:
            break

    return all_snaps


def parse_chain(snaps, spot):
    """Parse option snapshots into a DataFrame."""
    rows = []
    for sym, v in snaps.items():
        m = re.match(r"[A-Z]+(\d{6})([CP])(\d{8})", sym)
        if not m:
            continue
        exp    = "20" + m.group(1)[:2] + "-" + m.group(1)[2:4] + "-" + m.group(1)[4:]
        otype  = m.group(2)   # C or P
        strike = int(m.group(3)) / 1000.0
        iv     = v.get("impliedVolatility")
        greeks = v.get("greeks", {})
        db     = v.get("dailyBar", {})
        oi_val = v.get("openInterest")

        rows.append({
            "exp":    exp,
            "type":   otype,
            "strike": strike,
            "iv":     iv,
            "delta":  greeks.get("delta"),
            "gamma":  greeks.get("gamma"),
            "vega":   greeks.get("vega"),
            "theta":  greeks.get("theta"),
            "vol":    db.get("v", 0) or 0,
            "oi":     oi_val or 0,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["iv"]).copy()
    df["exp"]    = pd.to_datetime(df["exp"])
    df["dte"]    = (df["exp"] - pd.Timestamp.now()).dt.days.clip(lower=0)
    df["moneyness"] = df["strike"] / spot
    return df


def compute_metrics(df, spot, ticker):
    """Extract the regression-useful daily metrics from parsed chain."""
    if df.empty:
        return None

    row = {"date": today_str, "ticker": ticker, "spot": round(spot, 4)}

    exps    = sorted(df["exp"].unique())
    nearest = exps[0] if exps else None
    second  = exps[1] if len(exps) > 1 else None

    # ── ATM IV (nearest expiry, strikes within 1% of spot) ───────────────────
    near_df  = df[df["exp"] == nearest] if nearest else pd.DataFrame()
    atm_band = 0.01
    atm_df   = near_df[near_df["moneyness"].between(1 - atm_band, 1 + atm_band)]

    atm_c = atm_df[atm_df["type"] == "C"]["iv"].mean() if not atm_df.empty else np.nan
    atm_p = atm_df[atm_df["type"] == "P"]["iv"].mean() if not atm_df.empty else np.nan
    atm_avg = np.nanmean([atm_c, atm_p])

    row.update({
        "atm_iv_c":   round(atm_c,   6) if not np.isnan(atm_c)   else None,
        "atm_iv_p":   round(atm_p,   6) if not np.isnan(atm_p)   else None,
        "atm_iv_avg": round(atm_avg, 6) if not np.isnan(atm_avg) else None,
    })

    # ── OTM skew: put IV (92-97% moneyness) minus call IV (103-108%) ─────────
    otm_p_df = near_df[(near_df["type"] == "P") & near_df["moneyness"].between(0.92, 0.97)]
    otm_c_df = near_df[(near_df["type"] == "C") & near_df["moneyness"].between(1.03, 1.08)]
    if not otm_p_df.empty and not otm_c_df.empty:
        skew = otm_p_df["iv"].mean() - otm_c_df["iv"].mean()
        row["iv_skew_otm"] = round(skew, 6)
    else:
        row["iv_skew_otm"] = None

    # ── IV term structure slope (front - second expiry ATM IV) ───────────────
    if second is not None:
        sec_df  = df[(df["exp"] == second) & df["moneyness"].between(1 - atm_band, 1 + atm_band)]
        sec_avg = sec_df["iv"].mean() if not sec_df.empty else np.nan
        slope   = atm_avg - sec_avg if not np.isnan(sec_avg) else np.nan
        row["iv_term_slope"] = round(slope, 6) if not np.isnan(slope) else None
    else:
        row["iv_term_slope"] = None

    # ── put/call ratios (volume and OI, all expirations) ─────────────────────
    c_vol = df[df["type"] == "C"]["vol"].sum()
    p_vol = df[df["type"] == "P"]["vol"].sum()
    c_oi  = df[df["type"] == "C"]["oi"].sum()
    p_oi  = df[df["type"] == "P"]["oi"].sum()

    row["pcr_vol"]         = round(p_vol / c_vol, 6) if c_vol > 0 else None
    row["pcr_oi"]          = round(p_oi  / c_oi,  6) if c_oi  > 0 else None
    row["total_call_vol"]  = int(c_vol)
    row["total_put_vol"]   = int(p_vol)

    # ── nearest expiry info ───────────────────────────────────────────────────
    row["nearest_exp"] = nearest.strftime("%Y-%m-%d") if nearest else None
    row["nearest_dte"] = int(near_df["dte"].min()) if not near_df.empty else None

    # ── avg vega-weighted IV across all near-term contracts (VIX proxy) ──────
    near60 = df[df["dte"] <= 60].copy()
    if not near60.empty and near60["vega"].notna().any():
        near60_v = near60.dropna(subset=["vega"])
        total_vega = near60_v["vega"].abs().sum()
        if total_vega > 0:
            vw_iv = (near60_v["iv"] * near60_v["vega"].abs()).sum() / total_vega
            row["vega_weighted_iv"] = round(vw_iv, 6)
        else:
            row["vega_weighted_iv"] = None
    else:
        row["vega_weighted_iv"] = None

    return row


def append_row(ticker, row):
    out_path = os.path.join(OUT_DIR, f"{ticker}_options_daily.csv")
    new_df   = pd.DataFrame([row])

    if os.path.exists(out_path):
        existing = pd.read_csv(out_path)
        # overwrite today's row if already exists (idempotent)
        existing = existing[existing["date"] != today_str]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_csv(out_path, index=False)
    return out_path


# ── main ───────────────────────────────────────────────────────────────────────

results = []
for ticker in TICKERS:
    print(f"[optionsSnapshot] {ticker} @ {datetime.now().strftime('%H:%M:%S')}")
    try:
        spot   = get_spot(ticker)
        snaps  = get_options_chain(ticker, dte_max=60)
        df     = parse_chain(snaps, spot)
        if df.empty:
            print(f"  {ticker}: no options data with IV", file=sys.stderr)
            continue
        row    = compute_metrics(df, spot, ticker)
        if row is None:
            print(f"  {ticker}: could not compute metrics", file=sys.stderr)
            continue
        path   = append_row(ticker, row)
        print(f"  {ticker}: spot={spot:.2f}  ATM_IV={row.get('atm_iv_avg','n/a')}  "
              f"skew={row.get('iv_skew_otm','n/a')}  PCR_vol={row.get('pcr_vol','n/a')}  "
              f"-> {path}")
        results.append(row)
    except Exception as e:
        print(f"  {ticker}: ERROR - {e}", file=sys.stderr)

print(json.dumps({"status": "ok", "rows": len(results), "date": today_str}))
