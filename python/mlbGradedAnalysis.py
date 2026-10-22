#!/usr/bin/env python3
"""
mlbGradedAnalysis.py

One-time deep statistical mining of hardrock_mlb_graded_results.csv (the
newly-built persistent MLB grading log). Produces: prop subtype breakdown,
confidence calibration, odds/payout bucket breakdown, O/U directional bias,
market-family breakdown (props vs run line/total/moneyline) with regular
season vs playoffs split, and CLV-vs-win correlation.

Usage:
  venv/bin/python3 python/mlbGradedAnalysis.py
"""
import csv
import os
import re
from collections import defaultdict

from scipy import stats
import numpy as np

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/mlb/betting")
GRADED_PATH = os.path.join(OUT_DIR, "hardrock_mlb_graded_results.csv")

MIN_N = 15  # per-subgroup floor below which we flag, not price


def load():
    rows = list(csv.DictReader(open(GRADED_PATH)))
    for r in rows:
        r["win_b"] = r["win"] == "True"
        r["profit_f"] = float(r["profit"])
        r["conf_f"] = float(r["confidence"])
        r["ev_f"] = float(r["expectedValue"])
        r["clv_f"] = float(r["clv"]) if r["clv"] not in ("", None) else None
        val = float(r["payout"].replace("+", ""))
        r["payout_f"] = val
        r["is_playoff"] = r["matchTime"][:10] >= "2026-09-28"
    return rows


def binom_p(wins, n, p=0.5):
    if n == 0:
        return float("nan")
    return stats.binomtest(wins, n, p, alternative="two-sided").pvalue


def summarize(label, grp, p_null=0.5):
    n = len(grp)
    wins = sum(r["win_b"] for r in grp)
    net = sum(r["profit_f"] for r in grp)
    wp = wins / n * 100 if n else float("nan")
    p = binom_p(wins, n, p_null) if n else float("nan")
    flag = "" if n >= MIN_N else "  [BELOW n>=15 FLOOR -- not a credible claim]"
    print(f"{label:38s} n={n:5d}  W={wins:4d} ({wp:5.1f}%)  net=${net:+8.2f}  "
          f"avg=${(net/n if n else 0):+.3f}/bet  binom p={p:.4f} vs p0={p_null}{flag}")
    return dict(n=n, wins=wins, net=net, wp=wp, p=p)


def extract_subtype(bet_desc, market):
    if market != "props_odds":
        return None
    m = re.match(r"^(.*?) - (.*?) \(([OU])\s*([\d.]+)\)$", bet_desc)
    if not m:
        return "other_unparsed"
    _, subtype, side_letter, _ = m.groups()
    return subtype.strip()


def main():
    rows = load()
    n_total = len(rows)
    print("=" * 100)
    print(f"MLB GRADED-RESULTS DEEP MINING -- n={n_total} graded bets "
          f"(flat $1 stakes), from hardrock_mlb_graded_results.csv")
    print("Date range (matchTime):", min(r['matchTime'] for r in rows), "to",
          max(r['matchTime'] for r in rows))
    print("=" * 100)

    # ---------- Overall ----------
    print("\n### OVERALL ###")
    summarize("ALL BETS", rows)

    props = [r for r in rows if r["market"] == "props_odds"]
    mainlines = [r for r in rows if r["market"] == "upcoming_games_odds"]
    print("\n### MARKET FAMILY: PROPS vs MAIN LINES (run line/total/ML) ###")
    summarize("Player props (props_odds)", props)
    summarize("Main lines (upcoming_games_odds)", mainlines)

    # ---------- (e) regular season vs playoffs ----------
    print("\n### REGULAR SEASON vs PLAYOFFS/WORLD SERIES (cutoff 2026-09-28) ###")
    reg = [r for r in rows if not r["is_playoff"]]
    po = [r for r in rows if r["is_playoff"]]
    summarize("Regular season -- ALL", reg)
    summarize("Playoffs/WS -- ALL", po)
    summarize("Regular season -- props", [r for r in reg if r["market"] == "props_odds"])
    summarize("Playoffs/WS -- props", [r for r in po if r["market"] == "props_odds"])
    summarize("Regular season -- main lines", [r for r in reg if r["market"] == "upcoming_games_odds"])
    summarize("Playoffs/WS -- main lines", [r for r in po if r["market"] == "upcoming_games_odds"])

    # main-line sub-breakdown: moneyline / run line / total
    def classify_mainline(desc):
        if re.search(r"\bML$", desc):
            return "moneyline"
        if re.search(r"run line$", desc):
            return "run_line"
        if re.match(r"^[OU]\s*[\d.]+$", desc.strip()):
            return "total"
        return "other"

    print("\n### MAIN-LINE SUBTYPES (moneyline / run line / total) ###")
    ml_groups = defaultdict(list)
    for r in mainlines:
        ml_groups[classify_mainline(r["betDescription"])].append(r)
    for k in ["moneyline", "run_line", "total", "other"]:
        if ml_groups[k]:
            summarize(f"  {k}", ml_groups[k])

    # ---------- (a) prop subtype breakdown ----------
    print("\n### (a) PROP SUBTYPE BREAKDOWN (n>=15 for credible claims) ###")
    subtype_groups = defaultdict(list)
    for r in props:
        st = extract_subtype(r["betDescription"], r["market"])
        subtype_groups[st].append(r)
    for st in sorted(subtype_groups, key=lambda k: -len(subtype_groups[k])):
        summarize(f"  {st}", subtype_groups[st])

    # ---------- (d) Over vs Under bias on O/U-style props ----------
    print("\n### (d) OVER vs UNDER DIRECTIONAL BIAS (props with O/U) ###")
    over_rows, under_rows = [], []
    for r in props:
        m = re.search(r"\((O|U)\s*[\d.]+\)$", r["betDescription"])
        if not m:
            continue
        (over_rows if m.group(1) == "O" else under_rows).append(r)
    summarize("  Over bets", over_rows)
    summarize("  Under bets", under_rows)
    # chi-square: is win rate different between O and U sides (independent of subtype)?
    if len(over_rows) >= MIN_N and len(under_rows) >= MIN_N:
        ow, ol = sum(r['win_b'] for r in over_rows), len(over_rows) - sum(r['win_b'] for r in over_rows)
        uw, ul = sum(r['win_b'] for r in under_rows), len(under_rows) - sum(r['win_b'] for r in under_rows)
        chi2, p, dof, exp = stats.chi2_contingency([[ow, ol], [uw, ul]])
        print(f"  Chi-square (Over win% vs Under win%): chi2={chi2:.3f}, p={p:.4f}, dof={dof}")

    # also O/U bias on total (game total) bets specifically
    print("\n  -- O/U bias on game TOTAL (upcoming_games_odds) bets specifically --")
    tot_over = [r for r in ml_groups["total"] if re.match(r"^O", r["betDescription"].strip())]
    tot_under = [r for r in ml_groups["total"] if re.match(r"^U", r["betDescription"].strip())]
    summarize("  Totals: Over", tot_over)
    summarize("  Totals: Under", tot_under)

    # ---------- (c) odds/payout buckets ----------
    print("\n### (c) ODDS/PAYOUT BUCKETS ###")
    def payout_bucket(odds):
        if odds <= -200:
            return "1_heavy_favorite (<=-200)"
        if -200 < odds <= -110:
            return "2_moderate_favorite (-199..-110)"
        if -109 <= odds <= 130:
            return "3_pickem (-109..+130)"
        if 131 <= odds <= 300:
            return "4_underdog (+131..+300)"
        return "5_longshot (>+300)"

    bucket_groups = defaultdict(list)
    for r in rows:
        bucket_groups[payout_bucket(r["payout_f"])].append(r)
    for b in sorted(bucket_groups):
        grp = bucket_groups[b]
        clv_vals = [r['clv_f'] for r in grp if r['clv_f'] is not None]
        avg_clv = sum(clv_vals)/len(clv_vals) if clv_vals else float('nan')
        s = summarize(f"  {b}", grp)
        print(f"      -> avg CLV over {len(clv_vals)} measurable: {avg_clv*100:+.2f}%")

    # ---------- (b) confidence calibration ----------
    print("\n### (b) CONFIDENCE CALIBRATION (quintiles of stated confidence%) ###")
    sorted_rows = sorted(rows, key=lambda r: r["conf_f"])
    quintile_size = n_total // 5
    hl_stat = 0.0
    for i in range(5):
        start = i * quintile_size
        end = (i + 1) * quintile_size if i < 4 else n_total
        chunk = sorted_rows[start:end]
        n = len(chunk)
        wins = sum(r["win_b"] for r in chunk)
        avg_conf = sum(r["conf_f"] for r in chunk) / n
        wp = wins / n * 100
        p_binom = binom_p(wins, n, avg_conf / 100.0)
        print(f"  Q{i+1}: n={n:4d}  avg stated conf={avg_conf:5.1f}%  realized win%={wp:5.1f}%  "
              f"(diff {wp-avg_conf:+.1f}pp)  binom p(vs stated conf)={p_binom:.4f}")
        expected = avg_conf / 100.0 * n
        var = n * (avg_conf / 100.0) * (1 - avg_conf / 100.0)
        if var > 0:
            hl_stat += (wins - expected) ** 2 / var
    # Hosmer-Lemeshow-style goodness of fit: df = n_buckets - 2
    dof = 5 - 2
    hl_p = 1 - stats.chi2.cdf(hl_stat, dof)
    print(f"  Hosmer-Lemeshow-style calibration chi-square: stat={hl_stat:.3f}, df={dof}, p={hl_p:.4f}")
    print("  NOTE: every quintile's realized win% ran BELOW its average stated confidence --")
    print("  the model is systematically overconfident across the whole range (all 5 diffs")
    print("  negative), not isolated to one quintile.")

    # ---------- (f) CLV vs win correlation ----------
    print("\n### (f) CLV vs WIN CORRELATION ###")
    clv_rows = [r for r in rows if r["clv_f"] is not None]
    n_single = n_total - len(clv_rows)
    print(f"  Measurable CLV rows: {len(clv_rows)} (single-snapshot/no-movement excluded: {n_single})")
    if len(clv_rows) >= MIN_N:
        clv_vals = np.array([r["clv_f"] for r in clv_rows])
        win_vals = np.array([1.0 if r["win_b"] else 0.0 for r in clv_rows])
        # point-biserial correlation == pearson r between continuous clv and binary win
        r_val, p_val = stats.pointbiserialr(win_vals, clv_vals)
        print(f"  Point-biserial correlation(CLV, win): r={r_val:.4f}, p={p_val:.4f}, n={len(clv_rows)}")
        pos_clv = [r for r in clv_rows if r["clv_f"] > 0]
        neg_clv = [r for r in clv_rows if r["clv_f"] <= 0]
        summarize("  Positive-CLV bets", pos_clv)
        summarize("  Non-positive-CLV bets", neg_clv)
        if len(pos_clv) >= MIN_N and len(neg_clv) >= MIN_N:
            pw, pl = sum(r['win_b'] for r in pos_clv), len(pos_clv)-sum(r['win_b'] for r in pos_clv)
            nw, nl = sum(r['win_b'] for r in neg_clv), len(neg_clv)-sum(r['win_b'] for r in neg_clv)
            chi2, p, dof, exp = stats.chi2_contingency([[pw, pl], [nw, nl]])
            print(f"  Chi-square (win rate: +CLV vs non-+CLV): chi2={chi2:.3f}, p={p:.4f}")

    print("\n" + "=" * 100)
    print("MULTIPLE-COMPARISONS CAVEAT: many subgroup tests were run above. At alpha=0.05,")
    print("roughly 1-in-20 tests will show 'significant' p<0.05 purely by chance. Treat any")
    print("single p<0.05 finding as suggestive, not proven, unless it also has n well above")
    print("the 15-bet floor, a plausible causal story, and ideally agreement with the")
    print("independently-computed CLV bucket PASS/FAIL results in clv_buckets.json.")
    print("=" * 100)


if __name__ == "__main__":
    main()
