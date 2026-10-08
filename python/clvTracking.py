#!/usr/bin/env python3
"""
clvTracking.py

Closing Line Value (CLV) analysis for the +EV bet logs (NFL hardrockEV.py /
MLB hardrockMlbEV.py). Both master logs are already APPEND-ONLY and retain
EVERY scrape of a given outcomeId over time (by design -- see hardrockEV.py's
own docstring: "the same outcomeId recurs across scrapes as odds move ...
lets us later measure line movement / CLV"), so CLV can be computed entirely
after the fact from history already on disk. No scraper changes needed.

CLV per bet, in implied-probability terms:
  flagged_prob  = implied prob (from raw American odds -- no devig, but the
                  SAME convention on both ends of the diff so any book vig
                  cancels out) at the FIRST snapshot where that outcomeId
                  showed a positive-EV edge (i.e. the earliest point we'd
                  have actually seen the edge and could have bet it).
  closing_prob  = implied prob at the LAST snapshot ever scraped for that
                  outcomeId. HardRock's "upcoming games" listing drops a
                  game once it starts, so in practice the last scrape here
                  IS the closing line.
  clv           = closing_prob - flagged_prob

Positive CLV = the market's implied probability of our side rose after we
flagged it, i.e. we got a better number than the closing line reflects --
the standard sharp-bettor signal of a real, repeatable edge, independent
of whether any single bet actually won (win/loss is noisy over a small
sample; CLV is a lower-variance read on whether the model is finding
real value).

Bets that only ever had ONE snapshot logged before the game (e.g. flagged
right at the last pre-game scrape, or a market that only appeared once)
have no line movement to measure -- these are counted separately
("single_snapshot") rather than silently averaged in as CLV of exactly 0.
"""
from collections import defaultdict


def american_to_implied_prob(odds_str):
    val = float(str(odds_str).strip().replace("+", ""))
    if val > 0:
        decimal = 1 + val / 100.0
    else:
        decimal = 1 + 100.0 / abs(val)
    return 1.0 / decimal


def compute_clv_for_event(event_rows, is_valid_model):
    """event_rows: every logged row (ALL snapshots, not just the one used
    for W/L grading) for one game. is_valid_model(modelStatus) -> bool,
    since NFL and MLB use different modelStatus vocabularies.

    Returns {outcomeId: {betDescription, flagged_odds, flagged_at,
    closing_odds, closing_at, clv, single_snapshot}} for every outcomeId
    that was ever flagged +EV at some point in this game's snapshots.
    """
    by_outcome = defaultdict(list)
    for r in event_rows:
        oid = r.get("outcomeId")
        if oid:
            by_outcome[oid].append(r)

    out = {}
    for oid, rows in by_outcome.items():
        rows_sorted = sorted(rows, key=lambda r: r["scrapedAt"])
        flagged = next(
            (r for r in rows_sorted
             if is_valid_model(r.get("modelStatus"))
             and r.get("expectedValue") not in (None, "")
             and float(r["expectedValue"]) > 0),
            None,
        )
        if flagged is None:
            continue  # never flagged +EV in this game -- no CLV to track
        closing = rows_sorted[-1]
        try:
            flagged_prob = american_to_implied_prob(flagged["hardrockOddsRaw"])
            closing_prob = american_to_implied_prob(closing["hardrockOddsRaw"])
        except (ValueError, KeyError, TypeError):
            continue
        out[oid] = {
            "betDescription": flagged["betDescription"],
            "flagged_odds": flagged["hardrockOddsRaw"],
            "flagged_at": flagged["scrapedAt"],
            "closing_odds": closing["hardrockOddsRaw"],
            "closing_at": closing["scrapedAt"],
            "clv": closing_prob - flagged_prob,
            "single_snapshot": flagged["scrapedAt"] == closing["scrapedAt"],
        }
    return out


def summarize_clv(clv_dicts):
    """clv_dicts: list of per-bet CLV dicts (values from one or more
    compute_clv_for_event() calls, merged). Returns (n_measurable,
    avg_clv_or_None, pct_beat_close_or_None, n_single_snapshot).

    single-snapshot bets are excluded from the average/pct (no movement
    was observable) but reported as a count so it's clear how much of the
    sample actually has CLV data.
    """
    measurable = [d for d in clv_dicts if not d["single_snapshot"]]
    n_single = len(clv_dicts) - len(measurable)
    if not measurable:
        return 0, None, None, n_single
    avg_clv = sum(d["clv"] for d in measurable) / len(measurable)
    pct_beat = sum(1 for d in measurable if d["clv"] > 0) / len(measurable)
    return len(measurable), avg_clv, pct_beat, n_single


def fmt_clv(clv):
    """+2.1% style formatting for a probability-space CLV value."""
    pct = clv * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"
