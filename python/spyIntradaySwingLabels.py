#!/usr/bin/env python3
"""
spyIntradaySwingLabels.py
============================
Labels intraday LOCAL SWING HIGHS/LOWS on 5-min SPY bars using a zigzag
filter (standard technical-analysis swing-point definition: a local
extremum is confirmed only once price has retraced at least
MIN_SWING_PCT from it in the opposite direction -- this is what makes
"swing high/low" a well-posed label instead of just "every bar that's
locally highest/lowest", which would be mostly noise at 5-min resolution).

Built per James's scope correction (Oct'26): previous intraday spikes
tried to fold 1-min-derived DAILY summary stats into the next-day
direction classifier and that was the wrong hypothesis test -- what he
actually wants is an intraday swing-trading signal: "sell at highs, buy
back at lows" within the session (standalone strategy, shorts allowed,
flat by close -- confirmed via clarify).

IMPORTANT: swing labels are inherently NON-CAUSAL (you only know a swing
high was real once price retraces below MIN_SWING_PCT afterward) -- they
are LABELS for supervised training, never used as live features. The
downstream model (spyIntradaySwingTrain.py) predicts P(swing high/low
forming AT OR NEAR the current bar) using ONLY features computed from
bars up to and including the current bar (strictly no future bars),
checked explicitly in that script.

Output: outputs/features/markets/spy_intraday_swing_labels.csv
  columns: date, trading_date, bar_idx_in_day, close,
           is_swing_high, is_swing_low,   (bar-exact zigzag pivot labels)
           swing_high_near, swing_low_near  (soft label: pivot within
                                              +/-LABEL_TOLERANCE_BARS of
                                              this bar -- makes the
                                              classification target less
                                              razor-thin / easier to learn
                                              a useful signal from without
                                              needing bar-perfect timing)

Usage:
  venv/bin/python3 python/spyIntradaySwingLabels.py
"""
import os
import numpy as np
import pandas as pd

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_intraday_5min_features.csv")
OUT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_intraday_swing_labels.csv")

import sys as _sys
MIN_SWING_PCT = float(_sys.argv[1]) if len(_sys.argv) > 1 else 0.0015
                            # Oct'26 retest (James): original 0.15% threshold
                            # produced a real-but-uneconomic signal (AUC 0.85+
                            # but mean edge/trade ~0.9bps, underwater above
                            # ~1bps round-trip cost). Testing larger minimum
                            # swings (0.4%/0.6%) so each captured move is
                            # bigger relative to fixed transaction costs, at
                            # the cost of far fewer trades/day.
LABEL_TOLERANCE_BARS = 1    # soft label: +/-1 bar (+/-5min) counts as "near" a pivot
OUT_PATH = OUT_PATH.replace(".csv", f"_pct{MIN_SWING_PCT*10000:.0f}bps.csv")


def zigzag_pivots(close, min_pct):
    """Standard zigzag: walk forward tracking the current trend direction
    (unknown until the first confirmed move), confirm a pivot only when
    price retraces min_pct from the extreme since the last confirmed
    pivot. Returns an array of pivot types aligned to `close`'s index:
    1 = confirmed swing high, -1 = confirmed swing low, 0 = no pivot.
    This is a WITHIN-DAY-ONLY zigzag (reset at each day boundary by the
    caller) so it never claims a swing based on a prior day's price action
    carrying into today's reversal (every day starts a fresh zigzag).
    """
    n = len(close)
    pivots = np.zeros(n, dtype=int)
    if n < 3:
        return pivots

    last_pivot_idx = 0
    last_pivot_price = close[0]
    trend = 0   # 0=undetermined, 1=up, -1=down
    extreme_idx = 0
    extreme_price = close[0]

    for i in range(1, n):
        price = close[i]
        if trend == 0:
            # Determine initial trend direction once price moves min_pct
            # away from the starting price in either direction.
            if price >= last_pivot_price * (1 + min_pct):
                trend = 1
                extreme_idx, extreme_price = i, price
            elif price <= last_pivot_price * (1 - min_pct):
                trend = -1
                extreme_idx, extreme_price = i, price
            continue

        if trend == 1:
            if price > extreme_price:
                extreme_idx, extreme_price = i, price
            elif price <= extreme_price * (1 - min_pct):
                # Confirmed swing high at extreme_idx
                pivots[extreme_idx] = 1
                last_pivot_idx, last_pivot_price = extreme_idx, extreme_price
                trend = -1
                extreme_idx, extreme_price = i, price
        else:  # trend == -1
            if price < extreme_price:
                extreme_idx, extreme_price = i, price
            elif price >= extreme_price * (1 + min_pct):
                # Confirmed swing low at extreme_idx
                pivots[extreme_idx] = -1
                last_pivot_idx, last_pivot_price = extreme_idx, extreme_price
                trend = 1
                extreme_idx, extreme_price = i, price

    return pivots


def main():
    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
    df = df.sort_values(["trading_date", "bar_idx_in_day"]).reset_index(drop=True)

    print(f"Loaded {len(df)} bars across {df['trading_date'].nunique()} trading days")
    print(f"MIN_SWING_PCT={MIN_SWING_PCT*100:.2f}%  LABEL_TOLERANCE_BARS={LABEL_TOLERANCE_BARS}")

    all_pivots = []
    n_days = 0
    swings_per_day = []
    for day, g in df.groupby("trading_date", sort=True):
        g = g.sort_values("bar_idx_in_day")
        close = g["close"].values.astype(float)
        pivots = zigzag_pivots(close, MIN_SWING_PCT)
        all_pivots.append(pd.Series(pivots, index=g.index))
        n_days += 1
        swings_per_day.append(int(np.sum(pivots != 0)))

    pivot_series = pd.concat(all_pivots).sort_index()
    df["pivot"] = pivot_series
    df["is_swing_high"] = (df["pivot"] == 1).astype(int)
    df["is_swing_low"] = (df["pivot"] == -1).astype(int)

    # Soft labels: dilate the bar-exact pivot by LABEL_TOLERANCE_BARS within
    # each day (never across day boundaries -- groupby keeps it per-day).
    def dilate(s, tol):
        if tol <= 0:
            return s
        return s.rolling(2 * tol + 1, center=True, min_periods=1).max().fillna(0).astype(int)

    df["swing_high_near"] = df.groupby("trading_date")["is_swing_high"].transform(
        lambda s: dilate(s, LABEL_TOLERANCE_BARS))
    df["swing_low_near"] = df.groupby("trading_date")["is_swing_low"].transform(
        lambda s: dilate(s, LABEL_TOLERANCE_BARS))

    print(f"\nTotal confirmed swing highs: {df['is_swing_high'].sum()}")
    print(f"Total confirmed swing lows:  {df['is_swing_low'].sum()}")
    print(f"Avg swings/day: {np.mean(swings_per_day):.2f}  "
          f"(min={min(swings_per_day)}, max={max(swings_per_day)}, median={np.median(swings_per_day):.1f})")
    print(f"Soft-label swing_high_near rate: {df['swing_high_near'].mean()*100:.1f}% of bars")
    print(f"Soft-label swing_low_near rate:  {df['swing_low_near'].mean()*100:.1f}% of bars")

    out_cols = ["date", "trading_date", "bar_idx_in_day", "close",
                "is_swing_high", "is_swing_low", "swing_high_near", "swing_low_near"]
    out = df[out_cols].copy()
    out.to_csv(OUT_PATH, index=False)
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
