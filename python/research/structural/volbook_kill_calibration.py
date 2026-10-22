#!/usr/bin/env python3
"""
volbook_kill_calibration.py - is the volBook.py kill rule calibrated?
======================================================================
Live kill rule (python/volBook.py docstring):
  (a) cumulative net P&L <= -4% of launch equity (checked after every close), OR
  (b) at 25 closed condors: win rate < 60% AND net P&L < 0.

Question: does that rule kill a book that has no edge, and leave alone a book
that has the edge the study found? Answered by simulation, 20,000 books per
scenario, seed 42, deterministic.

Per-position outcome distributions (dollars per 1 contract, x100 multiplier)
  EMPIRICAL  daily synthetic 30-day short-variance P&L (vol points per unit vega,
             outputs/research/structural/holdout_2011_2016_daily.csv, column
             pnl_vol, 2011-2026, hit rate 84%). Transform, stated up front:
               pnl_credit = clip(pnl_vol * 0.5 * credit_scale, -2*credit, +0.5*credit)
             credit = 1.10. The clip encodes the 50% profit take and the stop at a
             mark of 3x credit (loss of 2x credit). credit_scale is the smallest
             value at which the median winning outcome equals the take (so most
             winners hit the take, as they do live); it is reported, not tuned.
             The daily series is overlapping 30-day windows, so it is sampled at
             stride 2 (positions go on every 2-3 trading days) and then
             block-bootstrapped in blocks of 5 (about two weeks of entries) so that
             the loss clustering of vol shocks survives into the simulated books.
  STYLISED   independent of the synthetic series: win +0.55 (50% of credit) with
             probability p; loss -2.20 (stop) with probability (1-p)*0.6; loss
             uniform on [-2.20, 0] otherwise. p in {0.85 edge, 0.78 thin,
             0.70 no edge (EV about zero), 0.62 negative}. Draws are iid.

Sizing: 1 contract per position, equity $100,000, max loss per contract $390
(5-wide wing minus credit). The live sizer would put on floor(1% * 100k / 390) = 2
contracts; the equity stop is also reported at that size as a sensitivity because
the count-based trigger does not depend on size but the equity trigger does.

Pre-registered objective for the rule grid:
  false kill at p = 0.85 <= 5%  AND  missed kill at p = 0.70 by N+15 closed >= 80%
  (i.e. the no-edge book is killed by N+15 with probability >= 80%).
  Among rules meeting both, smallest N wins. If none qualifies, report the frontier.

Outputs (outputs/research/structural/)
  volbook_kill_scenarios.csv   live rule, per scenario
  volbook_kill_curves.csv      P(killed by n) vs n per scenario, live rule (for the chart)
  volbook_kill_grid.csv        every rule in the grid, per scenario
  volbook_kill_summary.md
  r/volbookKillPlot.R -> volbook_kill.png
"""
import os
import sys
import itertools
import numpy as np
import pandas as pd

ROOT = os.path.expanduser("~/discordBot")
OUT = os.path.join(ROOT, "outputs", "research", "structural")
EMP_FILE = os.path.join(OUT, "holdout_2011_2016_daily.csv")   # 2011-2026 full span, column pnl_vol
EMP_FALLBACK = os.path.join(OUT, "vrp_regime_daily.csv")       # 2016-2026, column varswap_pnl_vol

SEED = 42
N_BOOKS = 20000
MAX_POS = 60
BLOCK = 5
STRIDE = 2
CREDIT = 1.10
TAKE = 0.5 * CREDIT          # +0.55
STOP = -2.0 * CREDIT         # -2.20
MULT = 100.0
EQUITY = 100_000.0
MAX_LOSS = 390.0             # per contract, 5-wide minus credit
CONTRACTS = 1
LIVE_SIZE = int((0.01 * EQUITY) // MAX_LOSS)   # what the live sizer would do: 2

LIVE = dict(N=25, wr=0.60, net="net<0", eq=0.04)
P_LEVELS = [0.85, 0.78, 0.70, 0.62]


# ----------------------------------------------------------------------------- outcome matrices
def load_empirical():
    if os.path.exists(EMP_FILE):
        d = pd.read_csv(EMP_FILE)
        col = "pnl_vol"
        src = os.path.basename(EMP_FILE)
    else:
        d = pd.read_csv(EMP_FALLBACK)
        col = "varswap_pnl_vol"
        src = os.path.basename(EMP_FALLBACK)
    d = d.dropna(subset=[col]).sort_values("date")
    return d[col].to_numpy(), src, d["date"].min(), d["date"].max()


def empirical_outcomes(rng):
    pnl_vol, src, d0, d1 = load_empirical()
    winners = pnl_vol[pnl_vol > 0]
    med_win = float(np.median(winners))
    credit_scale = TAKE / (0.5 * med_win)         # smallest scale with median winner == take
    per_credit = np.clip(pnl_vol * 0.5 * credit_scale, STOP, TAKE)
    win_rate = float((per_credit > 0).mean())
    take_frac = float((per_credit >= TAKE - 1e-12).mean())
    stop_frac = float((per_credit <= STOP + 1e-12).mean())
    ev = float(per_credit.mean())
    # stride, then block bootstrap
    series = per_credit[::STRIDE]
    n = len(series)
    n_blocks = int(np.ceil(MAX_POS / BLOCK))
    starts = rng.integers(0, n - BLOCK + 1, size=(N_BOOKS, n_blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]).reshape(N_BOOKS, -1)[:, :MAX_POS]
    mat = series[idx] * MULT * CONTRACTS
    info = dict(source=src, date_min=d0, date_max=d1, n_daily=len(pnl_vol), median_winner_volpts=med_win,
                credit_scale=credit_scale, win_rate=win_rate, frac_at_take=take_frac, frac_at_stop=stop_frac,
                ev_credit=ev, ev_usd=ev * MULT, n_stride_series=n)
    return mat, info


def stylised_outcomes(rng, p):
    u = rng.random((N_BOOKS, MAX_POS))
    out = np.full((N_BOOKS, MAX_POS), TAKE)
    loss = u >= p
    u2 = rng.random((N_BOOKS, MAX_POS))
    stop = loss & (u2 < 0.6)
    partial = loss & ~stop
    out[stop] = STOP
    out[partial] = rng.uniform(STOP, 0.0, size=int(partial.sum()))
    ev = p * TAKE + (1 - p) * 0.6 * STOP + (1 - p) * 0.4 * (STOP / 2)
    info = dict(p=p, win_rate=p, ev_credit=ev, ev_usd=ev * MULT)
    return out * MULT * CONTRACTS, info


# ----------------------------------------------------------------------------- rule evaluation
def evaluate(mat, N, wr_thr, net_cond, eq_stop, size_mult=1.0):
    """mat: books x positions, dollars. Returns dict of arrays per book:
    kill_pos (int, 0 = never), trigger (0 none, 1 equity, 2 count)."""
    cum = np.cumsum(mat * size_mult, axis=1)
    wins = np.cumsum(mat > 0, axis=1)
    nb, npos = mat.shape
    kill_pos = np.zeros(nb, dtype=int)
    trigger = np.zeros(nb, dtype=int)
    # equity stop: first position where cum <= -eq_stop * EQUITY
    if eq_stop is not None:
        hit = cum <= -eq_stop * EQUITY
        any_hit = hit.any(axis=1)
        first = np.where(any_hit, hit.argmax(axis=1) + 1, 0)
    else:
        first = np.zeros(nb, dtype=int)
    # count rule at exactly N
    if N is not None and N <= npos:
        wr_at = wins[:, N - 1] / N
        cond = wr_at < wr_thr
        if net_cond == "net<0":
            cond &= cum[:, N - 1] < 0
        elif net_cond == "net<-maxloss":
            cond &= cum[:, N - 1] < -MAX_LOSS * CONTRACTS * size_mult
        count_pos = np.where(cond, N, 0)
    else:
        count_pos = np.zeros(nb, dtype=int)
    # earliest trigger wins
    for pos, code in ((first, 1), (count_pos, 2)):
        take = (pos > 0) & ((kill_pos == 0) | (pos < kill_pos))
        kill_pos[take] = pos[take]
        trigger[take] = code
    return kill_pos, trigger, cum


def p_killed_by(kill_pos, n):
    return float(((kill_pos > 0) & (kill_pos <= n)).mean())


# ----------------------------------------------------------------------------- main
def main():
    rng = np.random.default_rng(SEED)
    scenarios = {}
    mat, emp_info = empirical_outcomes(rng)
    scenarios["empirical"] = (mat, emp_info)
    for p in P_LEVELS:
        label = {0.85: "stylised p=0.85 (edge)", 0.78: "stylised p=0.78 (thin)",
                 0.70: "stylised p=0.70 (no edge)", 0.62: "stylised p=0.62 (negative)"}[p]
        scenarios[label] = stylised_outcomes(rng, p)

    print("empirical transform:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in emp_info.items()})

    # ---- live rule per scenario
    rows, curves = [], []
    for name, (m, info) in scenarios.items():
        kp, tr, cum = evaluate(m, LIVE["N"], LIVE["wr"], LIVE["net"], LIVE["eq"])
        kp2, tr2, _ = evaluate(m, LIVE["N"], LIVE["wr"], LIVE["net"], LIVE["eq"], size_mult=LIVE_SIZE)
        killed = kp > 0
        surv_final = cum[~killed, -1]
        r = dict(scenario=name, win_rate=info["win_rate"], ev_usd_per_contract=info["ev_usd"],
                 p_killed_by_25=p_killed_by(kp, 25), p_killed_by_40=p_killed_by(kp, 40), p_killed_by_60=p_killed_by(kp, 60),
                 share_trigger_equity=float((tr == 1).mean()), share_trigger_count=float((tr == 2).mean()),
                 median_pos_to_kill=float(np.median(kp[killed])) if killed.any() else np.nan,
                 surv_final_pnl_p5=float(np.percentile(surv_final, 5)) if len(surv_final) else np.nan,
                 surv_final_pnl_p50=float(np.percentile(surv_final, 50)) if len(surv_final) else np.nan,
                 surv_final_pnl_p95=float(np.percentile(surv_final, 95)) if len(surv_final) else np.nan,
                 all_books_final_pnl_p50=float(np.median(cum[:, -1])),
                 p_killed_by_60_at_live_size_2ct=p_killed_by(kp2, 60),
                 share_trigger_equity_at_2ct=float((tr2 == 1).mean()))
        rows.append(r)
        for n in range(1, MAX_POS + 1):
            curves.append(dict(scenario=name, positions=n, p_killed=p_killed_by(kp, n)))
    scen = pd.DataFrame(rows)
    scen.to_csv(os.path.join(OUT, "volbook_kill_scenarios.csv"), index=False)
    pd.DataFrame(curves).to_csv(os.path.join(OUT, "volbook_kill_curves.csv"), index=False)
    pd.set_option("display.width", 250)
    print(scen.round(3).to_string())

    # ---- grid
    grid_rows = []
    Ns = [20, 25, 30, 40]
    wrs = [0.55, 0.60, 0.65, 0.70]
    nets = ["net<0", "net<-maxloss", "none"]
    eqs = [0.03, 0.04, 0.06, None]
    for N, wr, net, eq in itertools.product(Ns, wrs, nets, eqs):
        rec = dict(N=N, wr_thr=wr, net_cond=net, eq_stop=(eq if eq is not None else "none"))
        for name, (m, info) in scenarios.items():
            kp, tr, cum = evaluate(m, N, wr, net, eq)
            key = {"empirical": "emp", "stylised p=0.85 (edge)": "p85", "stylised p=0.78 (thin)": "p78",
                   "stylised p=0.70 (no edge)": "p70", "stylised p=0.62 (negative)": "p62"}[name]
            rec[f"{key}_killed_by_N"] = p_killed_by(kp, N)
            rec[f"{key}_killed_by_N15"] = p_killed_by(kp, min(N + 15, MAX_POS))
            rec[f"{key}_killed_by_60"] = p_killed_by(kp, 60)
        rec["false_kill_p85"] = rec["p85_killed_by_N15"]
        rec["missed_kill_p70"] = 1 - rec["p70_killed_by_N15"]
        rec["kill_p70_by_N15"] = rec["p70_killed_by_N15"]
        rec["meets_objective"] = (rec["false_kill_p85"] <= 0.05) and (rec["kill_p70_by_N15"] >= 0.80)
        rec["is_live"] = (N == 25 and wr == 0.60 and net == "net<0" and eq == 0.04)
        grid_rows.append(rec)
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(os.path.join(OUT, "volbook_kill_grid.csv"), index=False)

    ok = grid[grid.meets_objective]
    live_row = grid[grid.is_live].iloc[0]
    # frontier: for each false-kill ceiling, best achievable kill-at-p70
    frontier = []
    for cap in [0.01, 0.02, 0.05, 0.10, 0.20, 0.30]:
        sub = grid[grid.false_kill_p85 <= cap]
        if len(sub):
            best = sub.sort_values(["kill_p70_by_N15", "N"], ascending=[False, True]).iloc[0]
            frontier.append(dict(false_kill_cap=cap, best_kill_p70_by_N15=best.kill_p70_by_N15, N=int(best.N),
                                 wr_thr=best.wr_thr, net_cond=best.net_cond, eq_stop=best.eq_stop,
                                 false_kill_p85=best.false_kill_p85))
    frontier = pd.DataFrame(frontier)
    print("\nrules meeting objective:", len(ok))
    print(frontier.round(3).to_string())

    # binomial reference
    from scipy.stats import binom
    binom_ref = {N: dict(se85=np.sqrt(0.85 * 0.15 / N), se70=np.sqrt(0.70 * 0.30 / N)) for N in Ns}

    # ---- summary
    L = []
    L.append("volBook kill-rule calibration (simulation, seed 42, 20,000 books per scenario)\n")
    L.append("Live rule: equity <= -4% of launch at any close, OR at exactly 25 closed: win rate < 60% AND net < 0.")
    L.append(f"Sizing 1 contract, equity $100k, max loss ${MAX_LOSS:.0f}/contract, stop loss = -2x credit = ${-STOP*MULT:.0f}/contract. "
             f"The live sizer would use {LIVE_SIZE} contracts; equity-trigger numbers at that size are in the scenarios CSV.\n")
    L.append("Empirical transform")
    L.append(f"  source {emp_info['source']} ({emp_info['date_min']} to {emp_info['date_max']}, {emp_info['n_daily']} daily rows), "
             f"stride {STRIDE}, block bootstrap blocks of {BLOCK}.")
    L.append(f"  per_credit = clip(pnl_vol * 0.5 * {emp_info['credit_scale']:.4f}, {STOP:+.2f}, {TAKE:+.2f}); "
             f"credit_scale is the smallest value with median winner = take (median winning pnl_vol {emp_info['median_winner_volpts']:.2f} vol pts).")
    L.append(f"  resulting win rate {emp_info['win_rate']:.3f}, {emp_info['frac_at_take']:.3f} of positions at the take, "
             f"{emp_info['frac_at_stop']:.3f} at the stop, EV ${emp_info['ev_usd']:+.0f} per contract.\n")
    L.append("Scenario table, live rule")
    L.append(scen.round(3).to_string(index=False))
    L.append("")
    L.append(f"False kill at p=0.85 by 40 closed: {live_row.p85_killed_by_N15:.3f}. "
             f"Missed kill at p=0.70 by 40 closed: {live_row.missed_kill_p70:.3f} (killed only {live_row.p70_killed_by_N15:.3f}).")
    L.append("")
    L.append("Binomial standard error of a win rate: " + ", ".join(f"n={N}: {v['se85']:.3f} at p=0.85, {v['se70']:.3f} at p=0.70" for N, v in binom_ref.items()))
    L.append("")
    L.append(f"Grid: {len(grid)} rules. Rules meeting the pre-registered objective (false kill <= 5% AND kill-no-edge by N+15 >= 80%): {len(ok)}.")
    if len(ok):
        best = ok.sort_values(["N", "false_kill_p85"]).iloc[0]
        L.append("Recommended: " + str(best.to_dict()))
    else:
        L.append("None. Frontier (best achievable P(kill no-edge book by N+15) at each false-kill ceiling):")
        L.append(frontier.round(3).to_string(index=False))
    L.append("")
    L.append("Top 10 rules by kill-at-p70 with false kill <= 5%:")
    L.append(grid[grid.false_kill_p85 <= 0.05].sort_values("kill_p70_by_N15", ascending=False).head(10)
             [["N", "wr_thr", "net_cond", "eq_stop", "false_kill_p85", "kill_p70_by_N15", "p62_killed_by_N15", "emp_killed_by_N15"]].round(3).to_string(index=False))
    L.append("")
    L.append("Top 10 rules by kill-at-p70 with false kill <= 10%:")
    L.append(grid[grid.false_kill_p85 <= 0.10].sort_values("kill_p70_by_N15", ascending=False).head(10)
             [["N", "wr_thr", "net_cond", "eq_stop", "false_kill_p85", "kill_p70_by_N15", "p62_killed_by_N15", "emp_killed_by_N15"]].round(3).to_string(index=False))
    with open(os.path.join(OUT, "volbook_kill_summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[-25:]))
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
