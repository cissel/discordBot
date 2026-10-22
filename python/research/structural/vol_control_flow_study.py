#!/usr/bin/env python3
"""
Pre-registered study: systematic vol-control / risk-parity fund flow mechanics on SPY.

Thesis (from practitioner literature: Barclays/DB vol-control notes, risk-parity
flow research, menthorq/convextrade vol-control-fund writeups, Oct 2026 press
on vol-control funds at 98th-pct equity allocation): vol-targeting funds
($300-500bn AUM) set exposure = vol_target / realized_vol and rebalance on a
lagging realized-vol window. This generates MECHANICAL, non-fundamental flow:
  - When realized vol RISES through a threshold, funds are forced to sell
    (de-risk) over the following days -> should show up as a FORWARD RETURN
    DRAG and/or elevated forward vol immediately after the crossing.
  - When realized vol FALLS through a threshold, funds re-lever (buy) ->
    forward return TAILWIND, but positioning becomes more fragile (negative
    skew / larger left tail on the next shock).
  - At very low trailing vol (<8% ann, "near max allocation"), the literature
    says funds are already near-maximum long with no further buying power -
    a "coiled spring": forward downside tail risk should be elevated even
    though mean forward return is unremarkable.

This is a DIFFERENT causal mechanism from GEX (dealer options hedging,
already tested in gex_dix_study.py: GEX PASSED as a realized-vol regime
predictor but FAILED for direction). Vol-control flow depends only on the
REALIZED VOL LEVEL AND ITS RATE OF CHANGE (21d vs 63d RV ratio as proxy for
the "1mo vs 3mo RV" trigger used in the vol-control literature), not on
options positioning. We reuse spy_vol_r21 / spy_vol_r63 already in
spy_features.csv (no new data fetch required).

Pre-registered hypotheses (fixed before computing any results):
  H1 level-regime:   RV21 bucket (<8%, 8-15%, 15-20%, >20% ann) vs forward
                     5d/21d return and forward 5d realized vol.
  H2 crossing-up:    RV21/RV63 ratio crosses above 1.5 (vol spike regime
                     starting) -> forward 5d return vs unconditional mean
                     (expect negative: de-risking drag).
  H3 crossing-down:  RV21/RV63 ratio crosses below 0.7 (vol compression,
                     re-leveraging) -> forward 5d return vs unconditional
                     (expect positive drift) AND forward 21d left-tail
                     (5th pct of fwd 21d return) vs unconditional (expect
                     more negative: fragility).
  H4 coiled-spring:  RV21 < 8th percentile trailing 3y -> forward 21d
                     downside semi-deviation vs unconditional (expect higher).

Pass rule per hypothesis (same discipline as gex_dix_study.py /
calendar_effects.py): Newey-West |t| >= 2.0 on the full sample AND same sign
in both halves (split at the sample midpoint by date). Bonferroni m = number
of hypothesis sub-cells with n >= 30. PASS if it clears both; PARTIAL if NW
t clears on full sample but one half disagrees in sign or magnitude is tiny;
FAIL otherwise.

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/vol_control_flow_study.py
"""
import numpy as np
import pandas as pd

REPO = '/home/jhcv/discordBot'
OUT = f'{REPO}/outputs/research/structural'
NW_LAGS = 5
N_BOOT = 10000
SEED = 20261021
rng = np.random.default_rng(SEED)


def nw_t(x, lags=NW_LAGS):
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
    df = pd.read_csv(f'{OUT.replace("/research/structural", "/../features/markets")}/spy_features.csv' if False else
                      f'{REPO}/outputs/features/markets/spy_features.csv', parse_dates=['date'])
    df = df[['date', 'SPY_ret', 'spy_vol_r21', 'spy_vol_r63']].dropna().sort_values('date').reset_index(drop=True)
    df['fwd1'] = df['SPY_ret'].shift(-1)
    df['fwd5'] = df['SPY_ret'].rolling(5).sum().shift(-5)
    df['fwd21'] = df['SPY_ret'].rolling(21).sum().shift(-21)
    df['fwd5_rv'] = df['SPY_ret'].rolling(5).std().shift(-5) * np.sqrt(252)
    df['ratio'] = df['spy_vol_r21'] / df['spy_vol_r63']
    return df


def half_split(df):
    mid = df['date'].iloc[len(df) // 2]
    return df[df['date'] < mid], df[df['date'] >= mid]


def test_cell(name, series_full, series_h1, series_h2, bench_full=None):
    t_full = nw_t(series_full)
    t1, t2 = nw_t(series_h1), nw_t(series_h2)
    sign_agree = np.sign(series_h1.mean()) == np.sign(series_h2.mean()) if len(series_h1) and len(series_h2) else False
    n = len(series_full)
    return dict(hyp=name, n=n, mean=float(series_full.mean()), nw_t=t_full,
                mean_H1=float(series_h1.mean()) if len(series_h1) else np.nan,
                mean_H2=float(series_h2.mean()) if len(series_h2) else np.nan,
                t_H1=t1, t_H2=t2, sign_agree=bool(sign_agree))


def verdict(row):
    if abs(row['nw_t']) >= 2.0 and row['sign_agree']:
        return 'PASS'
    if abs(row['nw_t']) >= 2.0 and not row['sign_agree']:
        return 'PARTIAL'
    return 'FAIL'


def main():
    df = load()
    h1a, h2a = half_split(df)
    rows = []

    # ---- H1: RV21 level buckets vs forward 5d/21d return & forward 5d RV
    bins = [0, 0.08, 0.15, 0.20, 10]
    labels = ['lt8', '8to15', '15to20', 'gt20']
    df['bucket'] = pd.cut(df['spy_vol_r21'], bins=bins, labels=labels)
    uncond_fwd5 = df['fwd5'].mean()
    uncond_fwd21 = df['fwd21'].mean()
    for b in labels:
        sub = df[df['bucket'] == b]
        sub1 = h1a[h1a['spy_vol_r21'].between(bins[labels.index(b)], bins[labels.index(b) + 1])]
        sub2 = h2a[h2a['spy_vol_r21'].between(bins[labels.index(b)], bins[labels.index(b) + 1])]
        if len(sub) < 30:
            continue
        excess5 = sub['fwd5'] - uncond_fwd5
        excess1 = sub1['fwd5'] - uncond_fwd5 if len(sub1) else pd.Series(dtype=float)
        excess2 = sub2['fwd5'] - uncond_fwd5 if len(sub2) else pd.Series(dtype=float)
        rows.append({**test_cell(f'H1_RVbucket_{b}_fwd5excess', excess5, excess1, excess2),
                     'family': 'H1_level'})
        rows.append({**test_cell(f'H1_RVbucket_{b}_fwd21excess',
                                  sub['fwd21'] - uncond_fwd21,
                                  sub1['fwd21'] - uncond_fwd21 if len(sub1) else pd.Series(dtype=float),
                                  sub2['fwd21'] - uncond_fwd21 if len(sub2) else pd.Series(dtype=float)),
                     'family': 'H1_level'})

    # ---- H2: ratio crosses above 1.5 (de-risking trigger)
    df['cross_up'] = (df['ratio'] > 1.5) & (df['ratio'].shift(1) <= 1.5)
    ev = df[df['cross_up']]
    e1 = h1a[h1a['date'].isin(ev['date'])]
    e2 = h2a[h2a['date'].isin(ev['date'])]
    if len(ev) >= 15:
        rows.append({**test_cell('H2_crossup_fwd5excess', ev['fwd5'] - uncond_fwd5,
                                  e1['fwd5'] - uncond_fwd5 if len(e1) else pd.Series(dtype=float),
                                  e2['fwd5'] - uncond_fwd5 if len(e2) else pd.Series(dtype=float)),
                     'family': 'H2_crossup'})

    # ---- H3: ratio crosses below 0.7 (re-leveraging trigger)
    df['cross_dn'] = (df['ratio'] < 0.7) & (df['ratio'].shift(1) >= 0.7)
    ev = df[df['cross_dn']]
    e1 = h1a[h1a['date'].isin(ev['date'])]
    e2 = h2a[h2a['date'].isin(ev['date'])]
    if len(ev) >= 15:
        rows.append({**test_cell('H3_crossdn_fwd5excess', ev['fwd5'] - uncond_fwd5,
                                  e1['fwd5'] - uncond_fwd5 if len(e1) else pd.Series(dtype=float),
                                  e2['fwd5'] - uncond_fwd5 if len(e2) else pd.Series(dtype=float)),
                     'family': 'H3_crossdn'})
        # fragility: forward 21d left tail after compression vs unconditional left tail
        tail_ev = np.percentile(ev['fwd21'].dropna(), 5) if ev['fwd21'].notna().sum() > 10 else np.nan
        tail_un = np.percentile(df['fwd21'].dropna(), 5)
        rows.append(dict(hyp='H3_fragility_p5_fwd21', n=len(ev), mean=tail_ev, nw_t=np.nan,
                          mean_H1=np.nan, mean_H2=np.nan, t_H1=np.nan, t_H2=np.nan,
                          sign_agree=tail_ev < tail_un, family='H3_crossdn', note=f'unconditional_p5={tail_un:.4f}'))

    # ---- H4: coiled spring, RV21 below rolling-3y 8th pct -> forward 21d downside semidev
    df['rv21_p8_3y'] = df['spy_vol_r21'].rolling(756, min_periods=250).quantile(0.08)
    df['coiled'] = df['spy_vol_r21'] < df['rv21_p8_3y']
    ev = df[df['coiled']]
    uncond_semidev = np.sqrt((df['fwd21'].clip(upper=0) ** 2).mean())
    ev_semidev = np.sqrt((ev['fwd21'].clip(upper=0) ** 2).mean()) if len(ev) > 10 else np.nan
    rows.append(dict(hyp='H4_coiled_spring_semidev', n=len(ev), mean=ev_semidev, nw_t=np.nan,
                      mean_H1=np.nan, mean_H2=np.nan, t_H1=np.nan, t_H2=np.nan,
                      sign_agree=(ev_semidev > uncond_semidev) if pd.notna(ev_semidev) else False,
                      family='H4_coiled', note=f'unconditional_semidev={uncond_semidev:.4f}'))

    res = pd.DataFrame(rows)
    from scipy import stats as sstats
    m = int((res['nw_t'].notna()).sum())
    # Two-sided raw p from the NW t (approx normal), then Bonferroni over the m
    # parametric cells actually tested (matches calendar_effects.py convention).
    res['raw_p'] = res['nw_t'].apply(lambda t: 2 * (1 - sstats.norm.cdf(abs(t))) if pd.notna(t) else np.nan)
    res['bonf_p'] = (res['raw_p'] * m).clip(upper=1.0)

    def verdict_bonf(r):
        if pd.isna(r['nw_t']):
            return 'PASS' if r['sign_agree'] else 'FAIL'
        if r['bonf_p'] < 0.10 and r['sign_agree']:
            return 'PASS'
        if abs(r['nw_t']) >= 2.0 and r['sign_agree']:
            return 'PARTIAL (fails Bonferroni m=%d)' % m
        return 'FAIL'

    res['verdict'] = res.apply(verdict_bonf, axis=1)
    res.to_csv(f'{OUT}/vol_control_flow_results.csv', index=False)

    n_pass = (res['verdict'] == 'PASS').sum()
    n_partial = (res['verdict'] == 'PARTIAL').sum()
    n_fail = (res['verdict'] == 'FAIL').sum()

    with open(f'{OUT}/vol_control_flow_summary.md', 'w') as f:
        f.write('# Vol-control / risk-parity fund flow mechanics (SPY, 2016-2026)\n\n')
        f.write('Proxy: RV21/RV63 ratio and RV21 level buckets, from existing spy_vol_r21/r63 '
                'features (n=%d days). Pre-registered H1-H4, see script docstring. '
                'Pass rule: |NW t| >= 2.0 on full sample AND sign agrees both halves '
                '(or directional check for the two non-parametric tail hypotheses).\n\n' % len(df))
        f.write(f'Result: {n_pass} PASS, {n_partial} PARTIAL, {n_fail} FAIL out of {len(res)} cells '
                f'({m} had a computable NW t-stat).\n\n')
        f.write(res.to_markdown(index=False))
        f.write('\n\nOVERALL VERDICT: ')
        if n_pass == 0:
            f.write('FAIL. No cell clears the pre-registered bar. The RV21/RV63 ratio and level '
                    'thresholds described in vol-control-fund literature do not show a stable, '
                    'both-halves-agreeing forward-return or tail-risk signature in SPY 2016-2026 '
                    'at daily resolution. Consistent with the existing GEX finding: realized-vol '
                    'regime variables are good vol/sizing signals but keep failing as directional '
                    'or forward-risk timing signals once tested with this repo\'s half-split + NW-t bar.\n')
        else:
            f.write(f'{n_pass} cell(s) cleared the full bar - see table above for which hypothesis '
                    'and consider moving to a WFCV strategy backtest for those specific cells only.\n')
    print(f'Wrote {OUT}/vol_control_flow_summary.md')
    print(res[['hyp', 'family', 'n', 'nw_t', 'mean_H1', 'mean_H2', 'verdict']].to_string(index=False))


if __name__ == '__main__':
    main()
