#!/usr/bin/env python3
"""
Pre-registered study: Cboe Implied Correlation (COR1M / COR3M) vs forward SPX
return, forward realized vol, and the dispersion/correlation risk premium.

Thesis (queue id=implied_correlation_dispersion): the correlation risk
premium (implied correlation of SPX components systematically exceeds
realized correlation) is a well-documented, separately-priced risk factor
(Driessen/Maenhout/Vilkov 2009, Branger et al 2019; dispersion-trade Sharpe
~0.6-0.85 in the literature). It is mechanically distinct from GEX (dealer
gamma positioning, already PASS for forward RV), the VIX9D/VIX3M term
structure (already PASS for forward RV, FAIL for forward return), and the
CBOE SKEW index (OTM put skew, already FAIL) - this tests the SPX-vs-
single-name correlation spread specifically.

Data:
  outputs/markets/cache/implied_corr_daily.csv  (fetchImpliedCorr.py) - Cboe
    COR1M/COR3M, free CDN history, 2006-01-03 to present.
  outputs/markets/cache/spy_gex_daily.csv - SPX price level, 2011+ (longest
    daily price series in this repo).
  outputs/markets/cache/VIX.csv - VIX close, used as the variance-swap
    strike proxy for H4 (same convention as vrp_cross_index_study.py).
  outputs/markets/cache/sector_bars_historical.csv - 11 SPDR sector ETF daily
    returns, 2016+; used to build a REALIZED correlation proxy (9 "core"
    sectors live since before 2016: XLB XLE XLF XLI XLK XLP XLU XLV XLY;
    XLC/XLRE excluded to keep the sector basket constant across the sample).
    realized_corr21 = trailing 21-trading-day mean pairwise correlation of
    the 9 sector return series (36 pairs), i.e. this repo's own realized
    counterpart to what Cboe's COR index measures on single names.

All signals use the trading day t close (COR1M/COR3M/VIX/realized_corr21 are
all public same-day after the close); forward windows start at t+1, so
there is a full-day lag between signal availability and the first day it is
traded on - no lookahead.

Pre-registered hypotheses (fixed before computing any results):
  H1 level-continuation (vol regime): COR3M full-sample quintile -> forward
     21-trading-day realized SPX vol, excess over the unconditional mean
     forward RV. Thesis: elevated implied correlation reflects systemic-risk
     pricing and should precede elevated realized vol (same direction as the
     GEX->RV and VIX-term->RV findings, different instrument).
  H2 spike-reversion: 5-day change in COR3M, top vs bottom quintile ->
     forward 5-trading-day SPX return, excess over the unconditional mean
     fwd5 return. Thesis: a sharp correlation spike coincides with a
     risk-off/correlated-selloff episode and should see a reversion bounce
     (negative relationship: big up-moves in COR3M -> positive fwd5 excess).
  H3 term-structure slope: COR1M vs COR3M inversion (COR1M > COR3M, the
     correlation-curve analogue of the VIX9D>VIX3M stress signal already
     tested) -> (a) forward 5d return excess, (b) forward 21d realized vol
     excess. Thesis: inversion (near-term single-name panic correlation
     > medium-term) is a stress signal, same direction expected as the
     VIX9D/VIX3M -> RV PASS finding, tested here on the correlation curve
     instead of the vol curve.
  H4 dispersion risk premium: dispersion_premium = COR3M - 100*realized_corr21
     (positive = implied correlation priced above trailing realized -
     classic dispersion-trade setup), full-sample quintile -> forward 21d
     short-variance P&L proxy, pnl_t = (K_t - RV_t)/(2*IV_t) with K_t=VIX_t
     (vol pts), RV_t = realized SPX vol over t+1..t+21 (vol pts, annualized),
     excess over the unconditional mean pnl. Thesis: a richer dispersion
     premium (wider implied-over-realized correlation spread) should precede
     more profitable index variance-selling, same construction as
     vrp_cross_index_study.py's P&L definition. Window: 2016+ only (limited
     by sector ETF history for realized_corr21).

Pass rule (per PRIOR_FINDINGS.md standing discipline): Newey-West |t| >= 2.0
on the full sample AND same sign in both halves (chronological split at the
full-sample midpoint, applied per-cell via date membership). Bonferroni
m = number of hypothesis cells with a computable NW t-stat and n >= 30;
bonf_p = min(1, raw_p * m). PASS requires bonf_p < 0.10 AND sign agrees both
halves. PARTIAL = clears |t|>=2.0 full sample but fails Bonferroni or one
half disagrees. FAIL otherwise. NW lags = forward horizon in trading days
(5 for fwd5 cells, 21 for fwd21/RV cells), matching the overlap length of
consecutive daily entries (vrp_regime_study.py convention).

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/implied_correlation_dispersion_study.py
"""
import numpy as np
import pandas as pd
from scipy import stats as sstats

REPO = '/home/jhcv/discordBot'
OUT = f'{REPO}/outputs/research/structural'
CORE_SECTORS = ['XLB', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLU', 'XLV', 'XLY']


def nw_t(x, lags):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return np.nan
    m = x.mean()
    u = x - m
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        s += 2.0 * w * np.sum(u[L:] * u[:-L]) / n
    se = np.sqrt(max(s, 0) / n)
    return float(m / se) if se > 0 else np.nan


def load():
    px = pd.read_csv(f'{REPO}/outputs/markets/cache/spy_gex_daily.csv', parse_dates=['date'])
    px = px[['date', 'price']].dropna().sort_values('date').drop_duplicates('date').reset_index(drop=True)
    px['ret'] = px['price'].pct_change()

    corr = pd.read_csv(f'{REPO}/outputs/markets/cache/implied_corr_daily.csv', parse_dates=['date'])
    vix = pd.read_csv(f'{REPO}/outputs/markets/cache/VIX.csv', parse_dates=['date']).rename(
        columns={'VIXCLS_val': 'vix'})

    df = px.merge(corr, on='date', how='inner').merge(vix, on='date', how='left')
    df = df.sort_values('date').reset_index(drop=True)
    df['cor3m_delta5'] = df['cor3m'].diff(5)
    df['term_inverted'] = df['cor1m'] > df['cor3m']

    # forward windows (start t+1, no lookahead: signal known at close of t)
    n = len(df)
    fwd5 = np.full(n, np.nan)
    fwd21 = np.full(n, np.nan)
    fwd21_rv = np.full(n, np.nan)
    ret = df['ret'].values
    for i in range(n):
        if i + 6 <= n - 1:
            fwd5[i] = np.nansum(ret[i + 1:i + 6])
        if i + 22 <= n - 1:
            w = ret[i + 1:i + 22]
            fwd21[i] = np.nansum(w)
            fwd21_rv[i] = np.nanstd(w, ddof=1) * np.sqrt(252) * 100  # vol points
    df['fwd5'] = fwd5
    df['fwd21'] = fwd21
    df['fwd21_rv'] = fwd21_rv

    # H4: realized correlation proxy from 9 core sector ETFs (2016+)
    sec = pd.read_csv(f'{REPO}/outputs/markets/cache/sector_bars_historical.csv', parse_dates=['date'])
    rc = sec[['date'] + [f'{s}_ret' for s in CORE_SECTORS]].dropna().reset_index(drop=True)
    vals = rc[[f'{s}_ret' for s in CORE_SECTORS]].values
    win = 21
    realized_corr = np.full(len(rc), np.nan)
    for i in range(win - 1, len(rc)):
        block = vals[i - win + 1:i + 1]
        cm = np.corrcoef(block, rowvar=False)
        iu = np.triu_indices_from(cm, k=1)
        realized_corr[i] = np.nanmean(cm[iu])
    rc['realized_corr21'] = realized_corr
    rc = rc[['date', 'realized_corr21']]

    df = df.merge(rc, on='date', how='left')
    df['dispersion_premium'] = df['cor3m'] - 100 * df['realized_corr21']

    # H4 variance-swap P&L proxy: K=VIX (vol pts, lag already satisfied - vix_t
    # known at close t), RV settled over t+1..t+21 (reuse fwd21_rv)
    df['vrp_pnl'] = (df['vix'] - df['fwd21_rv']) / (2 * df['vix'])

    return df


def half_split(df):
    mid = df['date'].iloc[len(df) // 2]
    return df[df['date'] < mid], df[df['date'] >= mid]


def test_cell(name, family, series_full, series_h1, series_h2, lags):
    t_full = nw_t(series_full, lags=lags)
    t1 = nw_t(series_h1, lags=lags)
    t2 = nw_t(series_h2, lags=lags)
    sign_agree = (np.sign(series_h1.mean()) == np.sign(series_h2.mean())
                  if len(series_h1) >= 10 and len(series_h2) >= 10 else False)
    return dict(hyp=name, family=family, n=len(series_full), mean=float(series_full.mean()),
                nw_t=t_full, mean_H1=float(series_h1.mean()) if len(series_h1) else np.nan,
                mean_H2=float(series_h2.mean()) if len(series_h2) else np.nan,
                t_H1=t1, t_H2=t2, sign_agree=bool(sign_agree))


def main():
    df = load()
    h1a, h2a = half_split(df)
    uncond_fwd5 = df['fwd5'].mean()
    uncond_fwd21_rv = df['fwd21_rv'].mean()
    uncond_vrp = df['vrp_pnl'].mean()
    rows = []

    # ---- H1: COR3M level quintile -> fwd21 realized-vol excess
    base1 = df.dropna(subset=['cor3m', 'fwd21_rv'])
    q = pd.qcut(base1['cor3m'], 5, labels=['Q1_low', 'Q2', 'Q3', 'Q4', 'Q5_high'])
    base1 = base1.assign(cor3m_q=q)
    for lbl in ['Q1_low', 'Q2', 'Q3', 'Q4', 'Q5_high']:
        sub = base1[base1['cor3m_q'] == lbl]
        sub1 = h1a[h1a['date'].isin(sub['date'])]
        sub2 = h2a[h2a['date'].isin(sub['date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'H1_cor3m_{lbl}_fwd21rvexcess', 'H1_level_vol',
                               sub['fwd21_rv'] - uncond_fwd21_rv,
                               sub1['fwd21_rv'] - uncond_fwd21_rv, sub2['fwd21_rv'] - uncond_fwd21_rv, lags=21))

    # ---- H2: 5d change in COR3M, top vs bottom quintile -> fwd5 return excess
    base2 = df.dropna(subset=['cor3m_delta5', 'fwd5'])
    dq = pd.qcut(base2['cor3m_delta5'], 5, labels=['D1_down', 'D2', 'D3', 'D4', 'D5_up'])
    base2 = base2.assign(delta_q=dq)
    for lbl in ['D1_down', 'D5_up']:
        sub = base2[base2['delta_q'] == lbl]
        sub1 = h1a[h1a['date'].isin(sub['date'])]
        sub2 = h2a[h2a['date'].isin(sub['date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'H2_corchg_{lbl}_fwd5excess', 'H2_spike_reversion',
                               sub['fwd5'] - uncond_fwd5,
                               sub1['fwd5'] - uncond_fwd5, sub2['fwd5'] - uncond_fwd5, lags=5))

    # ---- H3: COR1M>COR3M inversion -> (a) fwd5 return excess, (b) fwd21 RV excess
    base3a = df.dropna(subset=['term_inverted', 'fwd5'])
    for lbl, mask in [('H3_terminverted_fwd5excess', base3a['term_inverted']),
                       ('H3_termnormal_fwd5excess', ~base3a['term_inverted'])]:
        sub = base3a[mask]
        sub1 = h1a[h1a['date'].isin(sub['date'])]
        sub2 = h2a[h2a['date'].isin(sub['date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(lbl, 'H3_term_slope',
                               sub['fwd5'] - uncond_fwd5,
                               sub1['fwd5'] - uncond_fwd5, sub2['fwd5'] - uncond_fwd5, lags=5))

    base3b = df.dropna(subset=['term_inverted', 'fwd21_rv'])
    for lbl, mask in [('H3_terminverted_fwd21rvexcess', base3b['term_inverted']),
                       ('H3_termnormal_fwd21rvexcess', ~base3b['term_inverted'])]:
        sub = base3b[mask]
        sub1 = h1a[h1a['date'].isin(sub['date'])]
        sub2 = h2a[h2a['date'].isin(sub['date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(lbl, 'H3_term_slope',
                               sub['fwd21_rv'] - uncond_fwd21_rv,
                               sub1['fwd21_rv'] - uncond_fwd21_rv, sub2['fwd21_rv'] - uncond_fwd21_rv, lags=21))

    # ---- H4: dispersion premium quintile -> fwd21 short-variance P&L excess (2016+)
    base4 = df.dropna(subset=['dispersion_premium', 'vrp_pnl'])
    dq4 = pd.qcut(base4['dispersion_premium'], 5, labels=['Q1_low', 'Q2', 'Q3', 'Q4', 'Q5_high'])
    base4 = base4.assign(disp_q=dq4)
    for lbl in ['Q1_low', 'Q2', 'Q3', 'Q4', 'Q5_high']:
        sub = base4[base4['disp_q'] == lbl]
        sub1 = h1a[h1a['date'].isin(sub['date'])]
        sub2 = h2a[h2a['date'].isin(sub['date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'H4_dispprem_{lbl}_fwd21vrpexcess', 'H4_dispersion_premium',
                               sub['vrp_pnl'] - uncond_vrp,
                               sub1['vrp_pnl'] - uncond_vrp, sub2['vrp_pnl'] - uncond_vrp, lags=21))

    res = pd.DataFrame(rows)
    m = int(res['nw_t'].notna().sum())
    res['raw_p'] = res['nw_t'].apply(lambda t: 2 * (1 - sstats.norm.cdf(abs(t))) if pd.notna(t) else np.nan)
    res['bonf_p'] = (res['raw_p'] * m).clip(upper=1.0)

    def verdict(r):
        if pd.isna(r['nw_t']):
            return 'FAIL'
        if r['bonf_p'] < 0.10 and r['sign_agree']:
            return 'PASS'
        if abs(r['nw_t']) >= 2.0 and r['sign_agree']:
            return 'PARTIAL (fails Bonferroni m=%d)' % m
        if abs(r['nw_t']) >= 2.0 and not r['sign_agree']:
            return 'PARTIAL (half-split sign disagrees)'
        return 'FAIL'

    res['verdict'] = res.apply(verdict, axis=1)
    res.to_csv(f'{OUT}/implied_correlation_dispersion_results.csv', index=False)

    n_pass = (res['verdict'] == 'PASS').sum()
    n_partial = res['verdict'].str.startswith('PARTIAL').sum()
    n_fail = (res['verdict'] == 'FAIL').sum()
    best = res.reindex(res['nw_t'].abs().sort_values(ascending=False).index).iloc[0]

    with open(f'{OUT}/implied_correlation_dispersion_summary.md', 'w') as f:
        f.write('# Cboe Implied Correlation (COR1M/COR3M) vs forward SPX return / '
                f'realized vol / dispersion premium (n={len(df)} trading days, '
                f'{df["date"].min().date()} to {df["date"].max().date()})\n\n')
        f.write('Data: Cboe COR1M/COR3M free CDN history (fetchImpliedCorr.py), SPX price '
                '(spy_gex_daily.csv), VIX (VIX.csv), realized correlation of 9 core SPDR '
                'sector ETFs (sector_bars_historical.csv, 2016+, H4 only). Signal known at '
                'close of day t, forward windows start t+1 (no lookahead).\n\n')
        f.write('Pre-registered H1 (COR3M level quintile -> fwd21 realized-vol continuation), '
                'H2 (COR3M 5d change, top/bottom quintile -> fwd5 return reversion), H3 '
                '(COR1M>COR3M term inversion -> fwd5 return and fwd21 RV), H4 (dispersion '
                'premium = COR3M - 100*realized_corr21, quintile -> fwd21 short-variance P&L, '
                '2016+ only). Pass rule: |NW t| >= 2.0 full sample AND Bonferroni-survives '
                '(m=%d cells) AND sign agrees both chronological halves.\n\n' % m)
        f.write(f'Result: {n_pass} PASS, {n_partial} PARTIAL, {n_fail} FAIL out of {len(res)} '
                f'cells ({m} had a computable NW t-stat).\n\n')
        f.write(res[['hyp', 'family', 'n', 'mean', 'nw_t', 'mean_H1', 'mean_H2', 't_H1', 't_H2',
                     'sign_agree', 'bonf_p', 'verdict']].to_markdown(index=False))
        f.write('\n\nStrongest cell by |NW t|: %s (nw_t=%.2f, bonf_p=%.3f, n=%d, verdict=%s)\n\n'
                % (best['hyp'], best['nw_t'], best['bonf_p'], best['n'], best['verdict']))
        f.write('OVERALL VERDICT: ')
        if n_pass == 0:
            f.write('FAIL. No cell clears the pre-registered bar (|NW t|>=2.0 + Bonferroni + '
                    'both-halves sign agreement). The implied-correlation / dispersion-premium '
                    'mechanism does not show a stable forward-return, forward-vol, or '
                    'variance-selling signature in this repo\'s sample once corrected for '
                    'multiple testing. Closed - do not re-propose this family.\n')
        else:
            f.write(f'{n_pass} cell(s) cleared the full bar - see table above. Move the '
                    'surviving hypothesis id(s) to status=backtest_strategy in the queue for a '
                    'WFCV strategy backtest before any further action.\n')
    print(f'Wrote {OUT}/implied_correlation_dispersion_summary.md')
    print(res[['hyp', 'family', 'n', 'nw_t', 't_H1', 't_H2', 'sign_agree', 'bonf_p', 'verdict']].to_string(index=False))


if __name__ == '__main__':
    main()
