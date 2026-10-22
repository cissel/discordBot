#!/usr/bin/env python3
"""
Pre-registered calendar / event-structure study on SPX (2011+) and SPY (2016+).

Effects tested (12, fixed before looking at results):
  TOM            last trading day of month through first 3 trading days of next month
  preFOMC        trading day before a scheduled FOMC decision
  FOMC           FOMC decision day
  OpExFri        monthly options expiration (3rd Friday, or Thursday before if holiday)
  OpExWeek       Mon-Fri of the OpEx week
  CPI            CPI release day (2016+ only, calendar coverage)
  NFP            NFP release day (2016+ only, calendar coverage)
  Mon..Fri       day of week

All event flags are knowable the day before (published schedules, exchange calendar).
Returns are close-to-close on the event day. Event vs non-event difference in mean
daily return, 10,000-draw bootstrap 90% CI, Welch t-stat, hit rate, n. Everything is
reported for pre-2019 and 2019+ separately, plus full period.

Multiple testing: m = number of (effect x period x series) cells with n >= 20,
Bonferroni p = min(1, p * m).

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/calendar_effects.py
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

REPO = '/home/jhcv/discordBot'
sys.path.insert(0, f'{REPO}/python')
from macro_event_calendars import FOMC_DATES, CPI_DATES, NFP_DATES  # noqa: E402

OUT = f'{REPO}/outputs/research/structural'
SPLIT = pd.Timestamp('2019-01-01')
N_BOOT = 10000
COST_PER_SIDE = 0.0001
rng = np.random.default_rng(20261019)

# Scheduled FOMC decision dates 2011-2015, hardcoded from public Federal Reserve
# meeting calendars (second day of each two-day meeting = statement release day).
FOMC_2011_2015 = [
    '2011-01-26', '2011-03-15', '2011-04-27', '2011-06-22', '2011-08-09', '2011-09-21', '2011-11-02', '2011-12-13',
    '2012-01-25', '2012-03-13', '2012-04-25', '2012-06-20', '2012-08-01', '2012-09-13', '2012-10-24', '2012-12-12',
    '2013-01-30', '2013-03-20', '2013-05-01', '2013-06-19', '2013-07-31', '2013-09-18', '2013-10-30', '2013-12-18',
    '2014-01-29', '2014-03-19', '2014-04-30', '2014-06-18', '2014-07-30', '2014-09-17', '2014-10-29', '2014-12-17',
    '2015-01-28', '2015-03-18', '2015-04-29', '2015-06-17', '2015-07-29', '2015-09-17', '2015-10-28', '2015-12-16',
]
FOMC_ALL = sorted(set(FOMC_2011_2015) | set(FOMC_DATES))


def load_series():
    spx = pd.read_csv(f'{REPO}/outputs/markets/cache/spy_gex_daily.csv', parse_dates=['date'])
    spx = spx[['date', 'price']].dropna().sort_values('date').drop_duplicates('date')
    spx['ret'] = spx['price'].pct_change()
    spx = spx.dropna(subset=['ret'])[['date', 'ret']].reset_index(drop=True)

    spy = pd.read_csv(f'{REPO}/outputs/markets/cache/SPY.csv', parse_dates=['date'])
    spy = spy.rename(columns={'SPY_ret': 'ret'}).dropna().sort_values('date').drop_duplicates('date')
    spy = spy[['date', 'ret']].reset_index(drop=True)
    return {'SPX': spx, 'SPY': spy}


def opex_dates(trading_days):
    """3rd Friday of each month; if not a trading day, the trading day before."""
    td = pd.DatetimeIndex(trading_days)
    out = []
    months = pd.period_range(td.min().to_period('M'), td.max().to_period('M'), freq='M')
    for m in months:
        first = m.to_timestamp()
        # weekday(): Mon=0 .. Fri=4
        offset = (4 - first.weekday()) % 7
        third_fri = first + pd.Timedelta(days=offset + 14)
        if third_fri in td:
            out.append(third_fri)
        else:
            prior = td[td < third_fri]
            if len(prior):
                out.append(prior[-1])
    return pd.DatetimeIndex(out)


def to_trading_day(dates, trading_days):
    """Map calendar dates to the trading day on or after (event schedule dates are
    announced in advance; if a date is not a trading day, nothing is flagged)."""
    td = pd.DatetimeIndex(trading_days)
    return pd.DatetimeIndex([d for d in pd.to_datetime(dates) if d in td])


def build_flags(df):
    td = pd.DatetimeIndex(df['date'])
    pos = pd.Series(np.arange(len(td)), index=td)
    flags = pd.DataFrame(index=td)

    # Turn of month: last trading day of month (-1) and first 3 trading days (+1..+3)
    month = td.to_period('M')
    is_last = np.r_[month[:-1] != month[1:], True]
    last_idx = np.where(is_last)[0]
    tom = np.zeros(len(td), bool)
    for i in last_idx:
        tom[i:min(i + 4, len(td))] = True
    flags['TOM'] = tom

    fomc = to_trading_day(FOMC_ALL, td)
    f = np.zeros(len(td), bool)
    f[pos[fomc].values] = True
    flags['FOMC'] = f
    pre = np.zeros(len(td), bool)
    idx = pos[fomc].values - 1
    pre[idx[idx >= 0]] = True
    flags['preFOMC'] = pre

    ox = opex_dates(td)
    o = np.zeros(len(td), bool)
    o[pos[ox].values] = True
    flags['OpExFri'] = o
    # OpEx week = Mon..Fri calendar week containing the 3rd Friday
    week = td.to_period('W-FRI')
    ox_weeks = set(ox.to_period('W-FRI'))
    flags['OpExWeek'] = np.array([w in ox_weeks for w in week])

    cpi = to_trading_day(CPI_DATES, td)
    c = np.zeros(len(td), bool)
    c[pos[cpi].values] = True
    flags['CPI'] = c
    nfp = to_trading_day(NFP_DATES, td)
    n = np.zeros(len(td), bool)
    n[pos[nfp].values] = True
    flags['NFP'] = n

    for i, name in enumerate(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']):
        flags[name] = td.weekday == i

    flags = flags.reset_index(drop=True)
    return pd.concat([df.reset_index(drop=True), flags], axis=1)


EFFECTS = ['TOM', 'preFOMC', 'FOMC', 'OpExFri', 'OpExWeek', 'CPI', 'NFP', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri']
MACRO_FROM_2016 = {'CPI', 'NFP'}


def analyse(df, effect, label):
    sub = df
    if effect in MACRO_FROM_2016:
        sub = df[df['date'] >= '2016-01-01']
    ev = sub.loc[sub[effect], 'ret'].values
    other = sub.loc[~sub[effect], 'ret'].values
    n_ev, n_ot = len(ev), len(other)
    row = {'effect': effect, 'period': label, 'n_event': n_ev, 'n_other': n_ot}
    if n_ev < 5 or n_ot < 5:
        return row
    diff = ev.mean() - other.mean()
    boots = (rng.choice(ev, (N_BOOT, n_ev), replace=True).mean(1)
             - rng.choice(other, (N_BOOT, n_ot), replace=True).mean(1))
    lo, hi = np.percentile(boots, [5, 95])
    t, p = stats.ttest_ind(ev, other, equal_var=False)
    row.update({
        'mean_event_bps': ev.mean() * 1e4,
        'mean_other_bps': other.mean() * 1e4,
        'diff_bps': diff * 1e4,
        'ci90_lo_bps': lo * 1e4,
        'ci90_hi_bps': hi * 1e4,
        't_stat': t,
        'p_value': p,
        'hit_rate_event': (ev > 0).mean(),
        'hit_rate_other': (other > 0).mean(),
        'ci_excludes_zero': bool(lo > 0 or hi < 0),
    })
    return row


def equity_curve(df, mask):
    """Long only on flagged days, else cash. 1bp per side on entry and exit."""
    r = df['ret'].values
    m = mask.values.astype(float)
    trades = np.abs(np.diff(np.r_[0.0, m]))  # 1 for each entry or exit
    strat = r * m - trades * COST_PER_SIDE
    return strat


def perf(strat):
    eq = np.cumprod(1 + strat)
    years = len(strat) / 252
    cagr = eq[-1] ** (1 / years) - 1
    vol = strat.std(ddof=1) * np.sqrt(252)
    sharpe = strat.mean() / strat.std(ddof=1) * np.sqrt(252) if strat.std() > 0 else np.nan
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return {'cagr': cagr, 'ann_vol': vol, 'sharpe': sharpe, 'max_dd': dd, 'total_return': eq[-1] - 1}


def main():
    series = load_series()
    rows = []
    for sname, df in series.items():
        df = build_flags(df)
        series[sname] = df
        periods = {
            'full': df,
            'pre2019': df[df['date'] < SPLIT],
            'post2019': df[df['date'] >= SPLIT],
        }
        for label, sub in periods.items():
            for eff in EFFECTS:
                r = analyse(sub, eff, label)
                r['series'] = sname
                r['start'] = sub['date'].min().date()
                r['end'] = sub['date'].max().date()
                rows.append(r)
    res = pd.DataFrame(rows)

    # Hypothesis count and Bonferroni: all cells with a computed p-value
    tested = res['p_value'].notna()
    m = int(tested.sum())
    res['n_tests_total'] = m
    res['p_bonferroni'] = np.minimum(1.0, res['p_value'] * m)
    res['sign_pre'] = np.nan
    # Stability: effect "passes" if CI excludes zero in BOTH halves with the same sign (per series)
    pass_rows = []
    for sname in series:
        for eff in EFFECTS:
            a = res[(res.series == sname) & (res.effect == eff) & (res.period == 'pre2019')].iloc[0]
            b = res[(res.series == sname) & (res.effect == eff) & (res.period == 'post2019')].iloc[0]
            same_sign = np.sign(a.get('diff_bps', np.nan)) == np.sign(b.get('diff_bps', np.nan))
            both_ci = bool(a.get('ci_excludes_zero', False)) and bool(b.get('ci_excludes_zero', False))
            pass_rows.append({'series': sname, 'effect': eff,
                              'passes_both_halves': bool(same_sign and both_ci)})
    passes = pd.DataFrame(pass_rows)
    res = res.merge(passes, on=['series', 'effect'], how='left')

    cols = ['series', 'effect', 'period', 'start', 'end', 'n_event', 'n_other', 'mean_event_bps', 'mean_other_bps',
            'diff_bps', 'ci90_lo_bps', 'ci90_hi_bps', 't_stat', 'p_value', 'p_bonferroni', 'n_tests_total',
            'hit_rate_event', 'hit_rate_other', 'ci_excludes_zero', 'passes_both_halves']
    res = res[cols]
    # long format: one row per effect x half x metric, as requested
    metrics = ['n_event', 'n_other', 'mean_event_bps', 'mean_other_bps', 'diff_bps', 'ci90_lo_bps', 'ci90_hi_bps',
               't_stat', 'p_value', 'p_bonferroni', 'hit_rate_event', 'hit_rate_other', 'ci_excludes_zero',
               'passes_both_halves']
    long = res.melt(id_vars=['series', 'effect', 'period', 'start', 'end', 'n_tests_total'],
                    value_vars=metrics, var_name='metric', value_name='value')
    long.to_csv(f'{OUT}/calendar_effects_results.csv', index=False)
    res.to_csv(f'{OUT}/calendar_effects_wide.csv', index=False)

    # Equity curves on SPX (longest history) for the 3 strongest full-period effects by |t|
    spx = series['SPX']
    full = res[(res.series == 'SPX') & (res.period == 'full')].copy()
    top3 = full.reindex(full['t_stat'].abs().sort_values(ascending=False).index).head(3)['effect'].tolist()
    eq_rows = []
    curves = pd.DataFrame({'date': spx['date']})
    bh = spx['ret'].values
    curves['BuyHold'] = np.cumprod(1 + bh)
    for label, sub_mask in [('full', spx['date'] >= spx['date'].min()), ('pre2019', spx['date'] < SPLIT),
                            ('post2019', spx['date'] >= SPLIT)]:
        sub = spx[sub_mask]
        p = perf(sub['ret'].values)
        eq_rows.append({'strategy': 'BuyHold', 'period': label, 'days_in_market': len(sub), **p})
        for eff in top3:
            s = equity_curve(sub, sub[eff])
            p = perf(s)
            eq_rows.append({'strategy': f'Long_{eff}_only', 'period': label,
                            'days_in_market': int(sub[eff].sum()), **p})
    for eff in top3:
        curves[f'Long_{eff}_only'] = np.cumprod(1 + equity_curve(spx, spx[eff]))
    eqdf = pd.DataFrame(eq_rows)
    eqdf.to_csv(f'{OUT}/calendar_effects_equity.csv', index=False)
    curves.to_csv(f'{OUT}/calendar_effects_curves.csv', index=False)

    pd.set_option('display.width', 250, 'display.max_columns', 30, 'display.float_format', '{:.3f}'.format)
    print(f'Total hypothesis cells tested (Bonferroni m): {m}')
    print('Top 3 SPX effects by |t|:', top3)
    print(res[['series', 'effect', 'period', 'n_event', 'diff_bps', 'ci90_lo_bps', 'ci90_hi_bps', 't_stat',
               'p_bonferroni', 'hit_rate_event', 'passes_both_halves']].to_string(index=False))
    print(eqdf.to_string(index=False))


if __name__ == '__main__':
    main()
