#!/usr/bin/env python3
"""
marketEarnings.py
Usage:
  python marketEarnings.py          -- upcoming earnings for next 7 days (full S&P 500)
  python marketEarnings.py AAPL     -- earnings history + future dates for a specific ticker

Output (no-arg mode):  ~/discordBot/outputs/markets/earnings_upcoming.csv
Output (ticker mode):  ~/discordBot/outputs/markets/earnings_ticker.csv
"""

import sys
import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import yfinance as yf

UPCOMING_PATH = os.path.expanduser("~/discordBot/outputs/markets/earnings_upcoming.csv")
TICKER_PATH   = os.path.expanduser("~/discordBot/outputs/markets/earnings_ticker.csv")

# Full S&P 500 constituent list (as of June 2026)
SP500_TICKERS = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB","ARE",
    "ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AEP","AXP","AIG","AMT",
    "AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON","APA","APO","AAPL","AMAT","APTV","ACGL",
    "ADM","ANET","AJG","AIZ","T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON","BKR","BALL","BAC",
    "BAX","BDX","BRK.B","BBY","TECH","BIIB","BLK","BX","BA","BCH","BSX","BMY","AVGO","BR","BRO",
    "BF.B","BLDR","BG","CDNS","CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CAT","CBOE","CBRE",
    "CDW","CE","COR","CNC","CNX","CDAY","CF","CRL","SCHW","CHTR","CVX","CMG","CB","CHD","CI","CINF",
    "CTAS","CSCO","C","CFG","CLX","CME","CMS","KO","CTSH","CL","CMCSA","CAG","COP","ED","STZ","CEG",
    "COO","CPRT","GLW","CPAY","CTVA","CSGP","COST","CTRA","CRWD","CCI","CSX","CMI","CVS","DHR","DHI",
    "DRI","DVA","DAY","DECK","DE","DAL","DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV",
    "DOW","DHI","DTE","DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","EMR","ENPH","ETR",
    "EOG","EPAM","EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EG","EVRG","ES","EXC","EXPE","EXPD",
    "EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB","FSLR","FE","FI","FMC","F","FTNT",
    "FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC",
    "GILD","GS","HAL","HIG","HAS","HCA","DOC","HSIC","HSY","HES","HPE","HLT","HOLX","HD","HON","HRL",
    "HST","HWM","HPQ","HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW","INCY","IR","PODD","INTC",
    "ICE","IFF","IP","IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ",
    "JCI","JPM","JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KLAC","KHC","KR","LHX","LH",
    "LRCX","LW","LVS","LDOS","LEN","LII","LYFT","LLY","LIN","LYV","LKQ","LMT","L","LOW","LULU","LYB",
    "MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA","MTCH","MKC","MCD","MCK","MDT","MRK","META",
    "MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO",
    "MS","MOS","MSI","MSCI","NDAQ","NTAP","NOC","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN",
    "NSC","NTRS","NOC","NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE",
    "ORCL","OTIS","OC","OGN","PCAR","PKG","PLTR","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG",
    "PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU","PEG","PTC","PSA","PHM",
    "QRVO","PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD","RVTY","ROK","ROL",
    "ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS","SJM","SNA",
    "SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI","SYF","SNPS","SYY","TMUS","TROW",
    "TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN","TXT","TMO","TJX","TSCO","TT",
    "TDG","TRV","TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS","URI","UNH",
    "UHS","VLO","VTR","VLTO","VRSN","VRSK","VZ","VRTX","VTRS","VICI","V","VST","VMC","WRB","GWW",
    "WAB","WBA","WMT","DIS","WBD","WM","WAT","WEC","WFC","WELL","WST","WDC","WY","WMB","WTW","WYNN",
    "XEL","XYL","YUM","ZBRA","ZBH","ZTS",
]

def safe_val(val):
    """Convert NaN / None / pandas NA to empty string."""
    try:
        import math
        if val is None:
            return ""
        if isinstance(val, float) and math.isnan(val):
            return ""
        return val
    except Exception:
        return ""


def fetch_ticker_earnings(ticker_sym):
    """
    Fetch earnings_dates DataFrame for a single ticker.
    Returns the DataFrame or None if unavailable.
    """
    try:
        t = yf.Ticker(ticker_sym)
        df = t.earnings_dates
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def get_company_name(ticker_sym):
    """Best-effort fetch of the company short name from .info."""
    try:
        info = yf.Ticker(ticker_sym).info
        return info.get("shortName", ticker_sym)
    except Exception:
        return ticker_sym


# ---------------------------------------------------------------------------
# Mode 1: specific ticker
# ---------------------------------------------------------------------------

def run_ticker_mode(ticker_sym):
    df = fetch_ticker_earnings(ticker_sym)
    if df is None:
        print(f"error: no earnings data for {ticker_sym}")
        sys.exit(1)

    # earnings_dates index is tz-aware datetime; sort ascending
    df = df.sort_index()

    now = datetime.now(timezone.utc)

    # Split into past and future
    past   = df[df.index <= now]
    future = df[df.index >  now]

    # Last 4 past + next 4 future
    selected = []
    for idx, row in list(past.iterrows())[-4:]:
        selected.append((idx, row))
    for idx, row in list(future.iterrows())[:4]:
        selected.append((idx, row))

    os.makedirs(os.path.dirname(TICKER_PATH), exist_ok=True)
    with open(TICKER_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "eps_estimate", "reported_eps", "surprise_pct"])
        for dt, row in selected:
            date_str     = dt.strftime("%Y-%m-%d")
            eps_est      = safe_val(row.get("EPS Estimate"))
            reported_eps = safe_val(row.get("Reported EPS"))
            surprise     = safe_val(row.get("Surprise(%)"))
            writer.writerow([date_str, eps_est, reported_eps, surprise])

    print("ok")


# ---------------------------------------------------------------------------
# Mode 2: upcoming earnings (next 7 days, full S&P 500 via calendar)
# ---------------------------------------------------------------------------

def _fetch_calendar(sym, now, week_end):
    """
    Worker: fetch .calendar for sym, return (sym, date) if earnings in window, else None.
    Uses .calendar which is much faster than .earnings_dates (no HTML scraping).
    """
    try:
        cal = yf.Ticker(sym).calendar
        if not cal:
            return None
        dates = cal.get("Earnings Date")
        if not dates:
            return None
        # dates is a list of datetime.date objects
        for d in dates:
            # Convert to tz-aware datetime for comparison
            if hasattr(d, 'year'):
                dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            else:
                dt = d
            if now <= dt <= week_end:
                # Also grab estimate from calendar if available
                eps_est = cal.get("Earnings Average", "")
                name    = cal.get("shortName", "")  # not always present
                return (sym, dt.date(), eps_est, name)
        return None
    except Exception:
        return None


def run_upcoming_mode():
    now      = datetime.now(timezone.utc)
    week_end = now + timedelta(days=7)

    results = []  # list of (date, ticker, company_name, eps_estimate)

    # Parallel fetch using ThreadPoolExecutor - 20 workers keeps it fast without hammering Yahoo
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_fetch_calendar, sym, now, week_end): sym for sym in SP500_TICKERS}
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                sym, dt_date, eps_est, name = res
                results.append((dt_date, sym, name, eps_est))

    # Sort by date ascending
    results.sort(key=lambda x: x[0])

    # Back-fill company names only for matched tickers (avoid hitting .info for all 500)
    matched_syms = list(dict.fromkeys(r[1] for r in results))  # unique, order-preserved
    name_map = {}
    for sym in matched_syms:
        name_map[sym] = get_company_name(sym)

    os.makedirs(os.path.dirname(UPCOMING_PATH), exist_ok=True)
    with open(UPCOMING_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "ticker", "company_name", "eps_estimate"])
        for dt_date, sym, _, eps_est in results:
            writer.writerow([
                str(dt_date),
                sym,
                name_map.get(sym, sym),
                eps_est if eps_est != "" else "",
            ])

    print("ok")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) >= 2:
        ticker_sym = sys.argv[1].upper().strip()
        run_ticker_mode(ticker_sym)
    else:
        run_upcoming_mode()


if __name__ == "__main__":
    main()
