#!/usr/bin/env python3
"""
Pre-registered study: CFTC "Traders in Financial Futures" (TFF) weekly
Leveraged-Funds positioning in CME S&P 500 futures vs forward SPX return.

Thesis (queue id=cot_positioning): CFTC weekly COT reports show
non-commercial / large-speculator net futures positioning. Literature is
mixed and unstable across subperiods (CXO Advisory R^2~0.02, sign flips;
Wang 2000 finds large-speculator positioning is a CONTINUATION signal while
hedger positioning is a weak CONTRARIAN one). This has never been tested in
this repo. We use the free OFR Hedge Fund Monitor mirror of the TFF report,
which only exposes the Leveraged Funds (hedge fund / CTA) breakdown for
S&P 500 futures (closest analog to "large speculator"): long/net/short
notional dollar positions, weekly, as-of every Tuesday, publicly released
the following Friday ~3:30pm ET.

Data: outputs/markets/cache/cot_sp500.csv (fetchCotSp500.py), 2013-03 to
2026-09 (707 weekly reports). SIGNAL DATE used throughout is release_date
(Friday), never as_of_date (Tuesday) - the as_of data was NOT public until
release_date, so any study using as_of_date directly would have lookahead.
net_ratio = lf_net / (lf_long + lf_short), bounded -1..1, removes most of
the index-price-level scaling present in raw notional-dollar figures.
Price series: outputs/markets/cache/spy_gex_daily.csv (SPX level, 2011+,
longest daily history available in this repo).

Pre-registered hypotheses (fixed before computing any results):
  H1 level-continuation: net_ratio quintile (full-sample quintile cutoffs)
     -> forward 4-week (21 trading day) excess return vs unconditional mean.
     Continuation thesis predicts Q5 (most net-long) excess > 0 and Q1
     (most net-short) excess < 0, monotonically.
  H2 flow-continuation: week-over-week change in net_ratio, top vs bottom
     quintile of the change -> forward 1-week (5 trading day) excess return.
     Continuation thesis (Wang 2000): funds buying more (top quintile of
     delta) -> positive forward drift; funds covering/selling (bottom
     quintile) -> negative forward drift.
  H3 extreme-contrarian: net_ratio in the bottom decile (extreme net SHORT,
     "max bearish") vs top decile (extreme net LONG, "max bullish") ->
     forward 4-week excess return. Contrarian thesis predicts the OPPOSITE
     sign from H1: extreme short -> positive forward excess (crowd wrong-footed
     into a squeeze), extreme long -> negative forward excess.
  H4 sign-persistence: net_ratio sign (net long vs net short at release) ->
     forward 1-week excess return, simple always-on directional test.

Pass rule (per PRIOR_FINDINGS.md standing discipline): Newey-West |t| >= 2.0
on the full sample AND same sign in both halves (chronological split at
sample midpoint). Bonferroni m = number of hypothesis cells with a
computable NW t-stat and n >= 30; bonf_p = min(1, raw_p * m). PASS requires
bonf_p < 0.10 AND sign agrees both halves. PARTIAL = clears |t|>=2.0 full
sample but fails Bonferroni or one half disagrees. FAIL otherwise.

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/cot_positioning_study.py
"""
import numpy as np
import pandas as pd
from scipy import stats as sstats

REPO = '/home/jhcv/discordBot'
OUT = f'{REPO}/outputs/research/structural'
SEED = 20261022
rng = np.random.default_rng(SEED)


def nw_t(x, lags=5):
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
    cot = pd.read_csv(f'{REPO}/outputs/markets/cache/cot_sp500.csv', parse_dates=['as_of_date', 'release_date'])
    cot = cot.sort_values('release_date').reset_index(drop=True)
    cot['net_ratio_delta'] = cot['net_ratio'].diff()

    px = pd.read_csv(f'{REPO}/outputs/markets/cache/spy_gex_daily.csv', parse_dates=['date'])
    px = px[['date', 'price']].dropna().sort_values('date').drop_duplicates('date').reset_index(drop=True)
    px['ret'] = px['price'].pct_change()
    trading_days = px['date'].values

    # Map each release_date (Friday, may be a market holiday) to the NEXT trading
    # day with data available, matching convention used in calendar_effects.py's
    # to_trading_day helper but rolling FORWARD since the signal becomes usable
    # only once markets reopen after the release.
    def next_trading_idx(d):
        idx = np.searchsorted(trading_days, np.datetime64(d), side='left')
        return idx if idx < len(trading_days) else None

    px = px.set_index('date')
    rows = []
    for _, r in cot.iterrows():
        idx = next_trading_idx(r['release_date'])
        if idx is None or idx + 21 >= len(px):
            continue
        sig_date = px.index[idx]
        fwd1 = px['ret'].iloc[idx + 1: idx + 6].sum()      # next 5 trading days after signal date
        fwd4 = px['ret'].iloc[idx + 1: idx + 22].sum()     # next 21 trading days
        rows.append({
            'release_date': r['release_date'], 'sig_date': sig_date,
            'net_ratio': r['net_ratio'], 'net_ratio_delta': r['net_ratio_delta'],
            'fwd1': fwd1, 'fwd4': fwd4,
        })
    df = pd.DataFrame(rows).dropna(subset=['net_ratio']).reset_index(drop=True)
    return df


def half_split(df):
    mid = df['sig_date'].iloc[len(df) // 2]
    return df[df['sig_date'] < mid], df[df['sig_date'] >= mid]


def test_cell(name, family, series_full, series_h1, series_h2, lags=4):
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
    uncond_fwd1 = df['fwd1'].mean()
    uncond_fwd4 = df['fwd4'].mean()
    rows = []

    # ---- H1: net_ratio quintiles (full-sample cutoffs) -> fwd4 excess
    q = pd.qcut(df['net_ratio'], 5, labels=['Q1_mostshort', 'Q2', 'Q3', 'Q4', 'Q5_mostlong'])
    df['nr_q'] = q
    cuts = pd.qcut(df['net_ratio'], 5, retbins=True)[1]
    for lbl in ['Q1_mostshort', 'Q2', 'Q3', 'Q4', 'Q5_mostlong']:
        sub = df[df['nr_q'] == lbl]
        sub1 = h1a[h1a['sig_date'].isin(sub['sig_date'])]
        sub2 = h2a[h2a['sig_date'].isin(sub['sig_date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'H1_netratio_{lbl}_fwd4excess', 'H1_level',
                               sub['fwd4'] - uncond_fwd4,
                               sub1['fwd4'] - uncond_fwd4, sub2['fwd4'] - uncond_fwd4, lags=4))

    # ---- H2: weekly net_ratio change, top vs bottom quintile -> fwd1 excess
    chg = df.dropna(subset=['net_ratio_delta']).copy()
    chg['delta_q'] = pd.qcut(chg['net_ratio_delta'], 5, labels=['D1_sell', 'D2', 'D3', 'D4', 'D5_buy'])
    for lbl in ['D1_sell', 'D5_buy']:
        sub = chg[chg['delta_q'] == lbl]
        sub1 = h1a[h1a['sig_date'].isin(sub['sig_date'])]
        sub2 = h2a[h2a['sig_date'].isin(sub['sig_date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'H2_flow_{lbl}_fwd1excess', 'H2_flow',
                               sub['fwd1'] - uncond_fwd1,
                               sub1['fwd1'] - uncond_fwd1, sub2['fwd1'] - uncond_fwd1, lags=1))

    # ---- H3: extreme deciles -> fwd4 excess (contrarian test)
    lo_cut = df['net_ratio'].quantile(0.10)
    hi_cut = df['net_ratio'].quantile(0.90)
    for lbl, sub in [('H3_extremeshort_decile', df[df['net_ratio'] <= lo_cut]),
                      ('H3_extremelong_decile', df[df['net_ratio'] >= hi_cut])]:
        sub1 = h1a[h1a['sig_date'].isin(sub['sig_date'])]
        sub2 = h2a[h2a['sig_date'].isin(sub['sig_date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'{lbl}_fwd4excess', 'H3_extreme',
                               sub['fwd4'] - uncond_fwd4,
                               sub1['fwd4'] - uncond_fwd4, sub2['fwd4'] - uncond_fwd4, lags=4))

    # ---- H4: sign persistence -> fwd1 excess
    for lbl, sub in [('H4_netlong_sign', df[df['net_ratio'] > 0]),
                      ('H4_netshort_sign', df[df['net_ratio'] < 0])]:
        sub1 = h1a[h1a['sig_date'].isin(sub['sig_date'])]
        sub2 = h2a[h2a['sig_date'].isin(sub['sig_date'])]
        if len(sub) < 30:
            continue
        rows.append(test_cell(f'{lbl}_fwd1excess', 'H4_sign',
                               sub['fwd1'] - uncond_fwd1,
                               sub1['fwd1'] - uncond_fwd1, sub2['fwd1'] - uncond_fwd1, lags=1))

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
    res.to_csv(f'{OUT}/cot_positioning_results.csv', index=False)

    n_pass = (res['verdict'] == 'PASS').sum()
    n_partial = res['verdict'].str.startswith('PARTIAL').sum()
    n_fail = (res['verdict'] == 'FAIL').sum()
    best = res.reindex(res['nw_t'].abs().sort_values(ascending=False).index).iloc[0]

    with open(f'{OUT}/cot_positioning_summary.md', 'w') as f:
        f.write('# CFTC TFF Leveraged-Funds S&P 500 positioning vs forward SPX return '
                f'(2013-2026, n={len(df)} weekly reports)\n\n')
        f.write('Data: OFR Hedge Fund Monitor mirror of CFTC Traders-in-Financial-Futures, '
                'Leveraged Funds long/net/short notional in CME S&P 500 futures. Signal date '
                '= report release_date (Friday, +3 cal days after as-of Tuesday), never the '
                'as-of date, to avoid lookahead. Price series: SPX (spy_gex_daily.csv).\n\n')
        f.write('Pre-registered H1 (level quintile continuation), H2 (weekly flow-change '
                'continuation), H3 (extreme-decile contrarian), H4 (sign persistence). '
                'Pass rule: |NW t| >= 2.0 full sample AND Bonferroni-survives (m=%d cells) '
                'AND sign agrees both chronological halves.\n\n' % m)
        f.write(f'Result: {n_pass} PASS, {n_partial} PARTIAL, {n_fail} FAIL out of {len(res)} '
                f'cells ({m} had a computable NW t-stat).\n\n')
        f.write(res[['hyp', 'family', 'n', 'mean', 'nw_t', 'mean_H1', 'mean_H2', 't_H1', 't_H2',
                     'sign_agree', 'bonf_p', 'verdict']].to_markdown(index=False))
        f.write('\n\nStrongest cell by |NW t|: %s (nw_t=%.2f, bonf_p=%.3f, n=%d, verdict=%s)\n\n'
                % (best['hyp'], best['nw_t'], best['bonf_p'], best['n'], best['verdict']))
        f.write('OVERALL VERDICT: ')
        if n_pass == 0:
            f.write('FAIL. No cell clears the pre-registered bar (|NW t|>=2.0 + Bonferroni + '
                    'both-halves sign agreement). Consistent with the mixed/unstable literature '
                    '(CXO Advisory, Wang 2000) this candidate was queued to re-check: CFTC '
                    'leveraged-fund positioning in SPX futures does not show a stable forward-'
                    'return signature in this repo\'s 2013-2026 sample once corrected for '
                    'multiple testing. Closed - do not re-propose this family.\n')
        else:
            f.write(f'{n_pass} cell(s) cleared the full bar - see table above. Move the '
                    'surviving hypothesis id(s) to status=backtest_strategy in the queue for a '
                    'WFCV strategy backtest before any further action.\n')
    print(f'Wrote {OUT}/cot_positioning_summary.md')
    print(res[['hyp', 'family', 'n', 'nw_t', 't_H1', 't_H2', 'sign_agree', 'bonf_p', 'verdict']].to_string(index=False))


if __name__ == '__main__':
    main()
