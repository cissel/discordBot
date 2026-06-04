#!/usr/bin/env python3
"""
marketOptions.py
Usage: python marketOptions.py <ticker> [expiry]
  ticker : stock symbol, e.g. AAPL
  expiry : optional date string like '2025-06-20'; defaults to nearest expiry

Outputs (~/discordBot/outputs/markets/):
  options_calls.csv  -- top-10 calls by volume within 15% of current price
  options_puts.csv   -- top-10 puts  by volume within 15% of current price
  options_meta.csv   -- ticker, expiry, current_price, expiry_used
Prints 'ok' on success.
"""

import sys
import os
import csv

# ---------------------------------------------------------------------------
# Validate args before importing heavy deps so errors surface fast
# ---------------------------------------------------------------------------
if len(sys.argv) < 2:
    print("error: usage: marketOptions.py <ticker> [expiry]", file=sys.stderr)
    sys.exit(1)

ticker_sym = sys.argv[1].upper().strip()
requested_expiry = sys.argv[2].strip() if len(sys.argv) >= 3 else None

# ---------------------------------------------------------------------------
try:
    import yfinance as yf
except ImportError as e:
    print(f"error: yfinance not available: {e}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets")
os.makedirs(OUT_DIR, exist_ok=True)

CALLS_CSV = os.path.join(OUT_DIR, "options_calls.csv")
PUTS_CSV  = os.path.join(OUT_DIR, "options_puts.csv")
META_CSV  = os.path.join(OUT_DIR, "options_meta.csv")

COL_NAMES = ["strike", "last", "bid", "ask", "volume", "oi", "iv", "itm"]

# Map our output column names -> DataFrame column names
DF_MAP = {
    "strike": "strike",
    "last":   "lastPrice",
    "bid":    "bid",
    "ask":    "ask",
    "volume": "volume",
    "oi":     "openInterest",
    "iv":     "impliedVolatility",
    "itm":    "inTheMoney",
}

# ---------------------------------------------------------------------------
def get_current_price(tk):
    """Try tk.info first, fall back to fast_info.last_price."""
    try:
        price = tk.info.get("currentPrice")
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    try:
        price = tk.fast_info["last_price"]
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    try:
        price = tk.fast_info.last_price
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    return None


def write_options_csv(path, df):
    """Write top-10 rows (already filtered/sorted) to path using COL_NAMES."""
    rows = []
    for _, row in df.head(10).iterrows():
        out_row = {}
        for col, src in DF_MAP.items():
            val = row.get(src, "")
            # Round floats to 4 dp for readability
            if isinstance(val, float):
                val = round(val, 4)
            out_row[col] = val
        rows.append(out_row)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COL_NAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    tk = yf.Ticker(ticker_sym)

    # ---- expiry selection --------------------------------------------------
    try:
        available = tk.options  # tuple of date strings
    except Exception as e:
        print(f"error: could not fetch options for {ticker_sym}: {e}", file=sys.stderr)
        sys.exit(1)

    if not available:
        print(f"error: no options available for {ticker_sym}", file=sys.stderr)
        sys.exit(1)

    if requested_expiry:
        if requested_expiry in available:
            expiry_used = requested_expiry
        else:
            print(
                f"error: expiry '{requested_expiry}' not found for {ticker_sym}. "
                f"Available: {list(available)}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        expiry_used = available[0]  # nearest expiry

    # ---- current price -----------------------------------------------------
    current_price = get_current_price(tk)
    if current_price is None:
        print(f"error: could not determine current price for {ticker_sym}", file=sys.stderr)
        sys.exit(1)

    # ---- option chain ------------------------------------------------------
    try:
        chain = tk.option_chain(expiry_used)
    except Exception as e:
        print(f"error: could not fetch option chain for {ticker_sym} / {expiry_used}: {e}", file=sys.stderr)
        sys.exit(1)

    calls = chain.calls.copy()
    puts  = chain.puts.copy()

    # ---- filter to within 15% of current price ----------------------------
    lo = current_price * 0.85
    hi = current_price * 1.15

    calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
    puts  = puts [(puts ["strike"] >= lo) & (puts ["strike"] <= hi)]

    # ---- sort by volume descending, top 10 --------------------------------
    calls = calls.sort_values("volume", ascending=False)
    puts  = puts .sort_values("volume", ascending=False)

    # ---- write output files ------------------------------------------------
    write_options_csv(CALLS_CSV, calls)
    write_options_csv(PUTS_CSV,  puts)

    with open(META_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "expiry", "current_price", "expiry_used"])
        writer.writeheader()
        writer.writerow({
            "ticker":        ticker_sym,
            "expiry":        requested_expiry if requested_expiry else "",
            "current_price": round(current_price, 4),
            "expiry_used":   expiry_used,
        })

    print("ok")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
