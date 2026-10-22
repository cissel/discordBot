#!/usr/bin/env python3
"""Pre-registered overnight vs intraday return decomposition on SPY, 1-minute bars 2021-2026.

Builds a reusable daily session table (spy_daily_sessions.csv), then runs the
pre-registered tests. Conditioning variables (GEX sign, VIX regime) are lagged
one day to avoid lookahead. Minute files are read one year at a time with usecols.
"""
import os
import sys
import numpy as np
import pandas as pd

REPO = '/home/jhcv/discordBot'
sys.path.insert(0, os.path.join(REPO, 'python'))
from macro_event_calendars import FOMC_DATES, CPI_DATES, NFP_DATES  # noqa: E402

OUT = os.path.join(REPO, 'outputs/research/structural')
os.makedirs(OUT, exist_ok=True)
YEARS = range(2021, 2027)
COST_BASE_BPS = 1.0  # per side baseline

# ---------------------------------------------------------------------------
# 1. Daily session table from minute bars (one year at a time)
# ---------------------------------------------------------------------------
rows = []
for yr in YEARS:
    fn = os.path.join(REPO, f'outputs/markets/intraday/SPY_{yr}_1min.csv')
    df = pd.read_csv(fn, usecols=['date', 'open', 'close', 'session'])
    ts = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
    df['day'] = ts.dt.date
    df['mins'] = ts.dt.hour * 60 + ts.dt.minute
    reg = df[(df['mins'] >= 570) & (df['mins'] < 960)]  # 09:30 <= t < 16:00 (bar start times)
    for day, g in reg.groupby('day'):
        g = g.sort_values('mins')
        first = g.iloc[0]
        last = g.iloc[-1]
        if first['mins'] > 575 or last['mins'] < 955:  # sanity: open within 5 min, close within 5 min
            continue
        b1000 = g[g['mins'] <= 599]
        b1530 = g[g['mins'] <= 929]
        rows.append(dict(
            date=pd.Timestamp(day),
            reg_open=first['open'],
            reg_close=last['close'],
            px_1000=b1000.iloc[-1]['close'],
            px_1530=b1530.iloc[-1]['close'],
            n_bars=len(g),
            session_label_regular=int((g['session'] == 'regular').mean() > 0.9),
        ))
    del df, reg
    print(f'{yr}: {sum(1 for r in rows if r["date"].year == yr)} days', flush=True)

daily = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
daily['prev_close'] = daily['reg_close'].shift(1)
daily['r_overnight'] = daily['reg_open'] / daily['prev_close'] - 1
daily['r_intraday'] = daily['reg_close'] / daily['reg_open'] - 1
daily['r_close_to_close'] = daily['reg_close'] / daily['prev_close'] - 1
daily['r_first30'] = daily['px_1000'] / daily['reg_open'] - 1
daily['r_last30'] = daily['reg_close'] / daily['px_1530'] - 1
daily['year'] = daily['date'].dt.year

# conditioning data, lagged one trading day
gex = pd.read_csv(os.path.join(REPO, 'outputs/markets/cache/spy_gex_daily.csv'), parse_dates=['date'])
gex = gex.sort_values('date')
gex['gex_prev'] = gex['gex'].shift(1)
vix = pd.read_csv(os.path.join(REPO, 'outputs/markets/cache/VIX.csv'), parse_dates=['date']).sort_values('date')
vix['VIXCLS_val'] = pd.to_numeric(vix['VIXCLS_val'], errors='coerce')
vix['vix_prev'] = vix['VIXCLS_val'].shift(1)
vix['vix_med252_prev'] = vix['VIXCLS_val'].shift(1).rolling(252, min_periods=200).median()

daily = daily.merge(gex[['date', 'gex_prev']], on='date', how='left')
daily = daily.merge(vix[['date', 'vix_prev', 'vix_med252_prev']], on='date', how='left')
daily['gex_prev_sign'] = np.where(daily['gex_prev'].isna(), np.nan, np.where(daily['gex_prev'] > 0, 1, -1))
daily['vix_high'] = np.where(daily['vix_prev'].isna() | daily['vix_med252_prev'].isna(), np.nan,
                             (daily['vix_prev'] > daily['vix_med252_prev']).astype(float))
ev = set(FOMC_DATES) | set(CPI_DATES) | set(NFP_DATES)
dstr = daily['date'].dt.strftime('%Y-%m-%d')
daily['fomc'] = dstr.isin(FOMC_DATES).astype(int)
daily['cpi'] = dstr.isin(CPI_DATES).astype(int)
daily['nfp'] = dstr.isin(NFP_DATES).astype(int)
daily['event_day'] = dstr.isin(ev).astype(int)

daily.to_csv(os.path.join(OUT, 'spy_daily_sessions.csv'), index=False)
d = daily.dropna(subset=['r_overnight']).copy()
print(f'daily table: {len(d)} days with overnight return, {d.date.min().date()} to {d.date.max().date()}')
print('session label check (share of days where >90% of 09:30-16:00 bars labelled regular):',
      round(d['session_label_regular'].mean(), 3))

# ---------------------------------------------------------------------------
# statistics helpers
# ---------------------------------------------------------------------------
results = []
HYP = [0]


def stat(r):
    r = pd.Series(r).dropna()
    n = len(r)
    if n < 2:
        return dict(n=n, mean_bps=np.nan, t=np.nan, sharpe=np.nan, cum_pct=np.nan)
    m = r.mean()
    s = r.std(ddof=1)
    return dict(n=n, mean_bps=m * 1e4, t=m / (s / np.sqrt(n)), sharpe=m / s * np.sqrt(252),
                cum_pct=((1 + r).prod() - 1) * 100)


def add(test, leg, cond, r, count=True):
    st = stat(r)
    results.append(dict(test=test, leg=leg, condition=cond, **st))
    if count:
        HYP[0] += 1
    return st


def diff_t(a, b):
    a, b = pd.Series(a).dropna(), pd.Series(b).dropna()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se


# ---------------------------------------------------------------------------
# Test 1: full-sample legs
# ---------------------------------------------------------------------------
for leg in ['r_overnight', 'r_intraday', 'r_close_to_close']:
    add('T1_full', leg, 'all', d[leg])
results.append(dict(test='T1_full', leg='overnight_minus_intraday', condition='all', n=len(d),
                    mean_bps=(d.r_overnight.mean() - d.r_intraday.mean()) * 1e4,
                    t=diff_t(d.r_overnight, d.r_intraday), sharpe=np.nan, cum_pct=np.nan))
HYP[0] += 1

# ---------------------------------------------------------------------------
# Test 2: per year
# ---------------------------------------------------------------------------
for yr, g in d.groupby('year'):
    for leg in ['r_overnight', 'r_intraday', 'r_close_to_close']:
        add('T2_year', leg, str(yr), g[leg], count=(leg == 'r_overnight'))

# ---------------------------------------------------------------------------
# Test 3: conditioning (prior-day GEX sign, prior-day VIX regime, event days)
# ---------------------------------------------------------------------------
conds = {
    'gex_prev_pos': d['gex_prev_sign'] == 1, 'gex_prev_neg': d['gex_prev_sign'] == -1,
    'vix_prev_high': d['vix_high'] == 1, 'vix_prev_low': d['vix_high'] == 0,
    'event_day': d['event_day'] == 1, 'non_event_day': d['event_day'] == 0,
    'fomc_day': d['fomc'] == 1, 'cpi_day': d['cpi'] == 1, 'nfp_day': d['nfp'] == 1,
}
for cname, mask in conds.items():
    for leg in ['r_overnight', 'r_intraday']:
        add('T3_cond', leg, cname, d.loc[mask, leg])
for pair in [('gex_prev_pos', 'gex_prev_neg'), ('vix_prev_high', 'vix_prev_low'), ('event_day', 'non_event_day')]:
    for leg in ['r_overnight', 'r_intraday']:
        a, b = d.loc[conds[pair[0]], leg], d.loc[conds[pair[1]], leg]
        results.append(dict(test='T3_cond_diff', leg=leg, condition=f'{pair[0]}_minus_{pair[1]}',
                            n=len(a) + len(b), mean_bps=(a.mean() - b.mean()) * 1e4, t=diff_t(a, b),
                            sharpe=np.nan, cum_pct=np.nan))
        HYP[0] += 1

# ---------------------------------------------------------------------------
# Test 4: first/last 30 min, last-30 predicting next overnight
# ---------------------------------------------------------------------------
add('T4_30min', 'r_first30', 'all', d['r_first30'])
add('T4_30min', 'r_last30', 'all', d['r_last30'])
reg = pd.DataFrame({'x': daily['r_last30'], 'y': daily['r_overnight'].shift(-1)}).dropna()
X = np.column_stack([np.ones(len(reg)), reg['x'].values])
beta, *_ = np.linalg.lstsq(X, reg['y'].values, rcond=None)
resid = reg['y'].values - X @ beta
sigma2 = resid @ resid / (len(reg) - 2)
cov = sigma2 * np.linalg.inv(X.T @ X)
t_beta = beta[1] / np.sqrt(cov[1, 1])
r2 = 1 - resid.var() / reg['y'].var()
results.append(dict(test='T4_regress', leg='next_overnight_on_last30', condition='slope', n=len(reg),
                    mean_bps=beta[1], t=t_beta, sharpe=np.nan, cum_pct=r2 * 100))
HYP[0] += 1
print(f'regression next-overnight ~ last30: beta={beta[1]:.4f} t={t_beta:.2f} R2={r2*100:.3f}% n={len(reg)}')

# ---------------------------------------------------------------------------
# Test 5: cost / break-even for overnight-only strategy (2 sides per day)
# ---------------------------------------------------------------------------
bh = stat(d['r_close_to_close'])


def on_sharpe(cost_bps):
    return stat(d['r_overnight'] - 2 * cost_bps / 1e4)['sharpe']


for c in [0, 0.5, 1.0, 2.0]:
    st = stat(d['r_overnight'] - 2 * c / 1e4)
    results.append(dict(test='T5_cost', leg='r_overnight_net', condition=f'cost_{c}bps_per_side', **st))
# break-even: Sharpe is monotone decreasing in cost, bisection
lo, hi = 0.0, 50.0
if on_sharpe(0) <= bh['sharpe']:
    be = 0.0
else:
    for _ in range(60):
        mid = (lo + hi) / 2
        if on_sharpe(mid) > bh['sharpe']:
            lo = mid
        else:
            hi = mid
    be = (lo + hi) / 2
results.append(dict(test='T5_cost', leg='breakeven_cost_bps_per_side', condition='sharpe_equals_buyhold',
                    n=len(d), mean_bps=be, t=np.nan, sharpe=bh['sharpe'], cum_pct=np.nan))
HYP[0] += 1

res = pd.DataFrame(results)
res['hypotheses_tested_total'] = HYP[0]
res.to_csv(os.path.join(OUT, 'overnight_results.csv'), index=False)

# cumulative curves for R
cum = d[['date', 'r_overnight', 'r_intraday', 'r_close_to_close']].copy()
for c in ['r_overnight', 'r_intraday', 'r_close_to_close']:
    cum['cum_' + c[2:]] = (1 + cum[c]).cumprod() - 1
cum.to_csv(os.path.join(OUT, 'overnight_cum.csv'), index=False)

# ---------------------------------------------------------------------------
# console report
# ---------------------------------------------------------------------------
pd.set_option('display.width', 200, 'display.max_rows', 200)
print('\nTOTAL HYPOTHESES TESTED:', HYP[0])
print(res.drop(columns='hypotheses_tested_total').round(3).to_string(index=False))
