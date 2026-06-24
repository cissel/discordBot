#!/usr/bin/env python3
"""
fetchMoonPhase.py
=================
Computes daily moon phase data for the full SPY feature date range.
No external API needed — pure astronomy via the `ephem` library.

Moon phase is 0.0 (new moon) -> 0.5 (full moon) -> 1.0 (new moon again).
Encoded as:
  moon_phase          : 0-1 continuous phase
  moon_phase_sin      : sin(2*pi*phase) — cyclic encoding
  moon_phase_cos      : cos(2*pi*phase) — cyclic encoding
  days_to_full_moon   : trading days until next full moon (capped 15)
  days_to_new_moon    : trading days until next new moon (capped 15)
  moon_full_flag      : 1 if within 2 days of full moon
  moon_new_flag       : 1 if within 2 days of new moon

Reference: Dichev & Janes (2003, Journal of Finance) found lunar
cycles predict equity returns cross-nationally.

Output: outputs/markets/cache/moon_phase_daily.csv
Usage:  venv/bin/python3 python/fetchMoonPhase.py
"""

import os
import math
import datetime
import pandas as pd

OUT_PATH = os.path.expanduser("~/discordBot/outputs/markets/cache/moon_phase_daily.csv")
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

# Date range matching SPY feature history
START = datetime.date(2015, 1, 1)
END   = datetime.date.today() + datetime.timedelta(days=5)  # buffer for next few days


def moon_phase_fraction(dt):
    """
    Returns moon illumination fraction 0.0-1.0 for a given date.
    Uses a simple astronomical formula (no ephem dependency):
    Based on known new moon epoch + synodic period.
    """
    # Known new moon: Jan 6, 2000 18:14 UTC (J2000 epoch reference)
    known_new = datetime.datetime(2000, 1, 6, 18, 14)
    synodic   = 29.53058867   # days
    dt_naive  = datetime.datetime(dt.year, dt.month, dt.day, 12, 0)
    elapsed   = (dt_naive - known_new).total_seconds() / 86400.0
    phase     = (elapsed % synodic) / synodic  # 0=new, 0.5=full, 1=new
    return phase


def days_to_phase(date, target_phase=0.5, max_days=20):
    """Days until next occurrence of target_phase (0=new, 0.5=full)."""
    synodic = 29.53058867
    for d in range(0, max_days + 1):
        check = date + datetime.timedelta(days=d)
        p = moon_phase_fraction(check)
        # Accept if within 0.035 of target (about 1 day tolerance)
        dist = min(abs(p - target_phase), abs(p - target_phase + 1), abs(p - target_phase - 1))
        if dist < 0.04:
            return d
    return max_days


print(f"[fetchMoonPhase] computing moon phases {START} to {END}...")
rows = []
current = START
while current <= END:
    phase     = moon_phase_fraction(current)
    phase_sin = math.sin(2 * math.pi * phase)
    phase_cos = math.cos(2 * math.pi * phase)
    dtf       = days_to_phase(current, target_phase=0.5, max_days=15)  # to full
    dtn       = days_to_phase(current, target_phase=0.0, max_days=15)  # to new
    full_flag = 1 if dtf <= 2 else 0
    new_flag  = 1 if dtn <= 2 else 0
    rows.append({
        "date":              current.strftime("%Y-%m-%d"),
        "moon_phase":        round(phase, 4),
        "moon_phase_sin":    round(phase_sin, 4),
        "moon_phase_cos":    round(phase_cos, 4),
        "days_to_full_moon": dtf,
        "days_to_new_moon":  dtn,
        "moon_full_flag":    full_flag,
        "moon_new_flag":     new_flag,
    })
    current += datetime.timedelta(days=1)

df = pd.DataFrame(rows)
df.to_csv(OUT_PATH, index=False)
print(f"  wrote {len(df)} rows -> {OUT_PATH}")
print(f"  full moon days: {df['moon_full_flag'].sum()}")
print(f"  new moon days:  {df['moon_new_flag'].sum()}")
