import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
import pandas_market_calendars as mcal
import sys
import os

# ── Args ──────────────────────────────────────────────────────────────────────
if len(sys.argv) < 3:
    print("Usage: python pull_trades.py <TICKER> <TIMEFRAME>")
    print("Timeframes: recent, close, open, day, full")
    sys.exit(1)

ticker    = sys.argv[1].upper()
timeframe = sys.argv[2].lower()

VALID_TIMEFRAMES = ["recent", "close", "open", "day", "full"]
if timeframe not in VALID_TIMEFRAMES:
    print(f"Invalid timeframe '{timeframe}'. Must be one of: {', '.join(VALID_TIMEFRAMES)}")
    sys.exit(1)

# ── Credentials ───────────────────────────────────────────────────────────────
API_KEY    = "AKGNBG6FMQEWRBELM45U"
API_SECRET = "86eND4Pe8NJp4wNoBzkFGrS2PAvHo3UhOy4xAIlL"
BASE_URL   = "https://data.alpaca.markets/v2"

# ── Find most recent trading day ───────────────────────────────────────────────
nyse   = mcal.get_calendar("NYSE")
et     = pytz.timezone("America/New_York")
now_et = datetime.now(et)

search_start = (now_et - timedelta(days=10)).strftime("%Y-%m-%d")
search_end   = now_et.strftime("%Y-%m-%d")
schedule     = nyse.schedule(start_date=search_start, end_date=search_end)

if schedule.empty:
    print("Could not find a recent trading day.")
    sys.exit(1)

last_trading_day = schedule.index[-1].date()

def make_window(hour_start, minute_start, hour_end, minute_end):
    d     = last_trading_day
    start = et.localize(datetime(d.year, d.month, d.day, hour_start, minute_start, 0))
    end   = et.localize(datetime(d.year, d.month, d.day, hour_end,   minute_end,   0))
    return start, end

# ── Build time window based on timeframe ──────────────────────────────────────
if timeframe == "recent":
    # Most recent hour of trading activity.
    # If we're currently within trading hours, look back 1 hour from now.
    # Otherwise fall back to the last hour of the most recent session (3-4 PM).
    now_decimal      = now_et.hour + now_et.minute / 60
    is_trading_hours = (now_et.date() == last_trading_day and 4.0 <= now_decimal <= 20.0)

    if is_trading_hours:
        window_end   = now_et.replace(second=0, microsecond=0)
        window_start = window_end - timedelta(hours=1)
        earliest     = et.localize(datetime(last_trading_day.year,
                                            last_trading_day.month,
                                            last_trading_day.day, 4, 0, 0))
        if window_start < earliest:
            window_start = earliest
    else:
        window_start, window_end = make_window(15, 0, 16, 0)

elif timeframe == "close":
    # Last hour of open market (3:00 PM - 4:00 PM ET)
    window_start, window_end = make_window(15, 0, 16, 0)

elif timeframe == "open":
    # First hour of open market (9:30 AM - 10:30 AM ET)
    window_start, window_end = make_window(9, 30, 10, 30)

elif timeframe == "day":
    # Regular trading day (9:30 AM - 4:00 PM ET)
    window_start, window_end = make_window(9, 30, 16, 0)

elif timeframe == "full":
    # Full session including pre-market and after-hours (4:00 AM - 8:00 PM ET)
    window_start, window_end = make_window(4, 0, 20, 0)

# Convert to UTC ISO8601
start_utc = window_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
end_utc   = window_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

print(f"Pulling [{timeframe}] trades for {ticker} on {last_trading_day}")
print(f"Window: {window_start.strftime('%I:%M %p')} – {window_end.strftime('%I:%M %p')} ET")

# ── Pull trades with pagination ────────────────────────────────────────────────
headers = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

all_trades = []
page_token = None

while True:
    params = {
        "start": start_utc,
        "end":   end_utc,
        "limit": 10000,
        "feed":  "sip",
    }
    if page_token:
        params["page_token"] = page_token

    resp = requests.get(
        f"{BASE_URL}/stocks/{ticker}/trades",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    trades = data.get("trades", [])
    all_trades.extend(trades)
    print(f"  Fetched {len(trades)} trades (total: {len(all_trades)})")

    page_token = data.get("next_page_token")
    if not page_token:
        break

if not all_trades:
    print(f"No trades returned for {ticker}.")
    sys.exit(1)

# ── Build DataFrame ────────────────────────────────────────────────────────────
df = pd.DataFrame(all_trades)

df = df.rename(columns={
    "t": "time",
    "p": "price",
    "s": "size",
    "x": "exchange",
    "i": "trade_id",
    "c": "conditions",
    "z": "tape",
})

df["time"]       = pd.to_datetime(df["time"], utc=True).dt.tz_convert("America/New_York")
first_price      = df["price"].iloc[0]
df["pct_change"] = ((df["price"] - first_price) / first_price) * 100
df               = df.sort_values("time").reset_index(drop=True)

print(f"Total trades: {len(df)}")
print(f"Price range:  ${df['price'].min():.2f} – ${df['price'].max():.2f}")

# ── Write CSV ──────────────────────────────────────────────────────────────────
out_dir  = "~/discordBot/outputs/markets"
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, f"{ticker}_trades.csv")

df.to_csv(out_path, index=False)
print(f"Saved to {out_path}")