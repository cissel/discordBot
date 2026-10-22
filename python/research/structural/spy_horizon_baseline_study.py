#!/usr/bin/env python3
"""
Pre-registered study: dumb baseline (D4) and horizon (D2) tests for the SPY daily strategy.

Everything below the PASS RULES line was fixed before any result was seen.

DATA
  outputs/features/markets/spy_features.csv, last row (today, provisional) dropped.
  SPY_ret = daily close-to-close simple return. Price index = cumprod(1 + SPY_ret).
  Feature lists are the production lists loaded via predictSpy.load_best_model
  (logistic next_dir_1d, gbm next_ret_5d). 10-day staleness rule: a feature whose
  last non-NaN date is more than 10 calendar days before the last row is dropped.
  NaNs imputed with training-fold medians only.

TARGETS
  next_ret_1d = SPY_ret[t+1]
  next_dir_1d = next_ret_1d > 0
  next_ret_5d = SPY_ret[t+1] + ... + SPY_ret[t+5]  (SUM of the next 5 daily simple
                returns, identical to the production definition in buildSpyFeatures)
  next_dir_5d = next_ret_5d > 0

WALK-FORWARD
  Test years 2019..2026 (2026 partial). For each test year the model is refit on
  every row dated before Jan 1 of that year whose target is fully realised before
  the first test row (so no 5-day label leaks across the boundary). Expanding window.

MODELS
  ML-1d: median impute, StandardScaler, L2 LogisticRegression, C chosen from
         {0.03, 0.1, 0.3} by inner 5-fold contiguous (unshuffled) KFold log loss.
         Trade long when p >= 0.51 else flat (live rule).
  5d logistic: same pipeline on next_dir_5d, long when p >= 0.51.
  5d GBM: GradientBoostingRegressor(depth 2, 150 trees, lr 0.05, subsample 0.8,
          random_state 42) on next_ret_5d, long when predicted return > 0.
  200dma: long for return t+1 when price index at close t > 200-day SMA through t.
  Always long. Intersection = 200dma AND p_1d >= 0.51.

COSTS
  0.25 bp per full round trip, i.e. 0.125 bp per unit of |position change|,
  charged on the day the position changes (close-to-close MOC execution).

METRICS
  Daily strategies: P&L_t = pos_{t-1} * SPY_ret_t - cost * |pos_{t-1} - pos_{t-2}|.
  Annualised return = mean daily P&L * 252 (arithmetic). Sharpe = mean/std * sqrt(252).
  Max drawdown on the cumulative additive P&L curve. Hit rate = share of traded days
  with positive next-day return. Trades per year = position changes per year.
  5d strategies: rebalance every 5th OOS trading day at one of five phase offsets
  (0..4). Position set at close of rebalance day t from the prediction at t, held
  for the non-overlapping weekly return sum(SPY_ret[t+1..t+5]). Weekly Sharpe
  = mean/std * sqrt(252/5). Reported per phase and averaged over the 5 phases.
  Per-year assignment uses the rebalance date.
  Newey-West (5 lags, Bartlett) t-stat on the daily P&L difference ML-1d minus 200dma.
  Spearman rho of predicted vs realised next_ret_5d on all OOS rows, with NW(5) t
  computed on the product of standardised ranks (whose mean is rho).

PASS RULES (written before results)
  D4 PASS (dumb baseline matches ML) if
      pooled Sharpe(200dma) >= pooled Sharpe(ML-1d) - 0.1
      AND NW t(ML-1d minus 200dma) < 2.0
  D2 PASS (5d has more signal per trade) if, for the 5d strategy (logistic or GBM
  evaluated separately, each must satisfy on its own):
      phase-averaged pooled Sharpe(5d) >= pooled Sharpe(ML-1d) + 0.2
      AND phase-averaged per-year Sharpe(5d) > per-year Sharpe(ML-1d) in >= 5 of 8 years
  Otherwise FAIL.

Run: /home/jhcv/discordBot/venv/bin/python3 python/research/structural/spy_horizon_baseline_study.py
"""
import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import KFold
from sklearn.metrics import log_loss

warnings.filterwarnings('ignore')
np.random.seed(42)
SEED = 42
REPO = '/home/jhcv/discordBot'
sys.path.insert(0, f'{REPO}/python')
import predictSpy as P  # noqa: E402  (import only)

OUT = f'{REPO}/outputs/research/structural'
YEARS = list(range(2019, 2027))
COST_PER_UNIT = 0.125e-4       # 0.25 bp per round trip, half per unit of |delta pos|
THRESH = 0.51
C_GRID = [0.03, 0.1, 0.3]
STALE_DAYS = 10
H = 5


# ----------------------------------------------------------------------------- helpers
def nw_tstat(x, lags=5):
    """Newey-West t-stat of the mean of series x with Bartlett kernel."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    m = x.mean()
    e = x - m
    s = np.sum(e * e)
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        s += 2.0 * w * np.sum(e[L:] * e[:-L])
    se = np.sqrt(s) / n
    return m / se if se > 0 else np.nan


def max_drawdown(pnl):
    c = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], c]))[1:]
    return float(np.min(c - peak))


def metrics(pnl, pos, ret, periods_per_year, years_span):
    pnl = np.asarray(pnl, float)
    pos = np.asarray(pos, float)
    ret = np.asarray(ret, float)
    traded = pos > 0
    hit = float(np.mean(ret[traded] > 0)) if traded.sum() > 0 else np.nan
    sd = pnl.std(ddof=1) if len(pnl) > 1 else np.nan
    sharpe = pnl.mean() / sd * np.sqrt(periods_per_year) if sd and sd > 0 else np.nan
    changes = np.abs(np.diff(np.concatenate([[0.0], pos]))).sum()
    return dict(n=len(pnl), hit_rate=hit, in_market=float(traded.mean()),
                ann_return=float(pnl.mean() * periods_per_year), sharpe=float(sharpe),
                max_dd=max_drawdown(pnl), trades_per_year=float(changes / years_span))


def stale_filter(df, feats):
    last = df['date'].max()
    keep, dropped = [], []
    for f in feats:
        if f not in df.columns:
            dropped.append((f, 'missing'))
            continue
        valid = df.loc[df[f].notna(), 'date']
        if valid.empty or (last - valid.max()).days > STALE_DAYS:
            dropped.append((f, 'stale' if not valid.empty else 'never'))
        else:
            keep.append(f)
    return keep, dropped


def impute_scale(Xtr, Xte):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    sc = StandardScaler().fit(Xtr)
    return sc.transform(Xtr), sc.transform(Xte)


def fit_logistic(Xtr, ytr, Xte):
    Xtr, Xte = impute_scale(Xtr, Xte)
    best_c, best_ll = None, np.inf
    kf = KFold(n_splits=5, shuffle=False)
    for C in C_GRID:
        lls = []
        for a, b in kf.split(Xtr):
            m = LogisticRegression(C=C, penalty='l2', max_iter=2000, random_state=SEED)
            m.fit(Xtr[a], ytr[a])
            lls.append(log_loss(ytr[b], m.predict_proba(Xtr[b])[:, 1], labels=[0, 1]))
        ll = float(np.mean(lls))
        if ll < best_ll:
            best_ll, best_c = ll, C
    m = LogisticRegression(C=best_c, penalty='l2', max_iter=2000, random_state=SEED).fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1], best_c


def fit_gbm(Xtr, ytr, Xte):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    m = GradientBoostingRegressor(n_estimators=150, max_depth=2, learning_rate=0.05,
                                  subsample=0.8, random_state=SEED).fit(Xtr, ytr)
    return m.predict(Xte)


# ----------------------------------------------------------------------------- data
df = pd.read_csv(f'{REPO}/outputs/features/markets/spy_features.csv', parse_dates=['date'])
df = df.sort_values('date').drop_duplicates('date').reset_index(drop=True)
today = pd.Timestamp.today().normalize()
df = df[df['date'] < today].reset_index(drop=True)
df = df.dropna(subset=['SPY_ret']).reset_index(drop=True)
print(f'rows {len(df)}  {df.date.min().date()} .. {df.date.max().date()}')

_, feats_1d = P.load_best_model('logistic', 'next_dir_1d')
_, feats_5d = P.load_best_model('gbm', 'next_ret_5d')
feats_1d, drop1 = stale_filter(df, list(feats_1d))
feats_5d, drop5 = stale_filter(df, list(feats_5d))
print(f'logistic feats kept {len(feats_1d)} dropped {drop1}')
print(f'gbm 5d feats kept {len(feats_5d)} dropped {drop5}')

r = df['SPY_ret'].values
df['next_ret_1d'] = df['SPY_ret'].shift(-1)
df['next_dir_1d'] = (df['next_ret_1d'] > 0).astype(float)
df['next_ret_5d'] = df['SPY_ret'].shift(-1).rolling(H).sum().shift(-(H - 1))
df['next_dir_5d'] = (df['next_ret_5d'] > 0).astype(float)
df['price'] = np.cumprod(1.0 + r)
df['sma200'] = df['price'].rolling(200).mean()
df['dma_sig'] = (df['price'] > df['sma200']).astype(float)   # known at close t, applied to t+1
df['year'] = df['date'].dt.year

# ----------------------------------------------------------------------------- walk-forward
df['p_1d'] = np.nan
df['p_5d'] = np.nan
df['pred_5d'] = np.nan
chosen_c = {}
for y in YEARS:
    test_idx = np.where(df['year'].values == y)[0]
    if len(test_idx) == 0:
        continue
    t0 = test_idx[0]
    tr1 = np.where((np.arange(len(df)) + 1 < t0) & df['next_ret_1d'].notna().values)[0]
    tr5 = np.where((np.arange(len(df)) + H < t0) & df['next_ret_5d'].notna().values)[0]
    X1 = df[feats_1d].values.astype(float)
    X5 = df[feats_5d].values.astype(float)
    p1, c1 = fit_logistic(X1[tr1], df['next_dir_1d'].values[tr1], X1[test_idx])
    p5, c5 = fit_logistic(X1[tr5], df['next_dir_5d'].values[tr5], X1[test_idx])
    g5 = fit_gbm(X5[tr5], df['next_ret_5d'].values[tr5], X5[test_idx])
    df.loc[test_idx, 'p_1d'] = p1
    df.loc[test_idx, 'p_5d'] = p5
    df.loc[test_idx, 'pred_5d'] = g5
    chosen_c[y] = (c1, c5)
    print(f'year {y}: train1d {len(tr1)} train5d {len(tr5)} test {len(test_idx)} C1d={c1} C5d={c5}')

oos = df[df['year'].isin(YEARS) & df['p_1d'].notna()].reset_index(drop=True)
n_oos = len(oos)
years_span = n_oos / 252.0

# ----------------------------------------------------------------------------- daily strategies
# Position decided at close t applies to the return on t+1. Shift everything so the
# P&L row is the day the return is earned. Cost charged on the position change.
def daily_pnl(pos_signal):
    """pos_signal[t] is the position chosen at close of OOS row t for row t+1."""
    pos = np.asarray(pos_signal, float)
    ret_next = oos['next_ret_1d'].values
    prev = np.concatenate([[0.0], pos[:-1]])
    pnl = pos * ret_next - COST_PER_UNIT * np.abs(pos - prev)
    return pnl, pos, ret_next

daily_strats = {
    'ML_1d_logistic': (oos['p_1d'].values >= THRESH).astype(float),
    'dma200': oos['dma_sig'].values,
    'always_long': np.ones(n_oos),
    'dma200_AND_ML': ((oos['p_1d'].values >= THRESH) & (oos['dma_sig'].values > 0)).astype(float),
}
# last OOS row has no realised next_ret_1d; drop it from P&L accounting
valid1 = oos['next_ret_1d'].notna().values

rows, pooled = [], []
daily_out = pd.DataFrame({'date': oos['date']})
for name, sig in daily_strats.items():
    pnl, pos, rn = daily_pnl(sig)
    daily_out[f'pnl_{name}'] = pnl
    daily_out[f'pos_{name}'] = pos
    m = metrics(pnl[valid1], pos[valid1], rn[valid1], 252, valid1.sum() / 252.0)
    pooled.append(dict(strategy=name, horizon='1d', phase='all', **m))
    for y in YEARS:
        k = valid1 & (oos['year'].values == y)
        if k.sum() < 20:
            continue
        rows.append(dict(strategy=name, horizon='1d', phase='all', year=y,
                         **metrics(pnl[k], pos[k], rn[k], 252, k.sum() / 252.0)))

# shift P&L to the date the return is earned (t+1) for the daily file
daily_out['date'] = daily_out['date'].shift(-1)
daily_out = daily_out.iloc[:-1].reset_index(drop=True)

diff = (daily_out['pnl_ML_1d_logistic'] - daily_out['pnl_dma200']).values
nw_ml_vs_dma = nw_tstat(diff, 5)
nw_ml_vs_always = nw_tstat((daily_out['pnl_ML_1d_logistic'] - daily_out['pnl_always_long']).values, 5)

# ----------------------------------------------------------------------------- 5d strategies
valid5 = oos['next_ret_5d'].notna().values
ret5 = oos['next_ret_5d'].values
sig5 = {
    '5d_logistic': (oos['p_5d'].values >= THRESH).astype(float),
    '5d_gbm': (oos['pred_5d'].values > 0).astype(float),
}
phase_pooled = {k: [] for k in sig5}
phase_year = {k: {y: [] for y in YEARS} for k in sig5}
# daily representation for chart/daily file: weekly position held on each of the 5 days
for name, s in sig5.items():
    daily_rep = np.zeros((5, n_oos))
    for ph in range(5):
        reb = np.arange(ph, n_oos, 5)
        reb = reb[valid5[reb]]
        pos = s[reb]
        rw = ret5[reb]
        prev = np.concatenate([[0.0], pos[:-1]])
        pnl = pos * rw - COST_PER_UNIT * np.abs(pos - prev)
        yrs = oos['year'].values[reb]
        m = metrics(pnl, pos, rw, 252.0 / H, len(reb) * H / 252.0)
        pooled.append(dict(strategy=name, horizon='5d', phase=str(ph), **m))
        phase_pooled[name].append(m)
        for y in YEARS:
            k = yrs == y
            if k.sum() < 4:
                continue
            my = metrics(pnl[k], pos[k], rw[k], 252.0 / H, k.sum() * H / 252.0)
            rows.append(dict(strategy=name, horizon='5d', phase=str(ph), year=y, **my))
            phase_year[name][y].append(my)
        # daily representation: position held t+1..t+5, cost on the day after rebalance
        for j, t in enumerate(reb):
            for d in range(1, H + 1):
                if t + d < n_oos:
                    daily_rep[ph, t + d] += pos[j] * oos['SPY_ret'].values[t + d]
            if t + 1 < n_oos:
                daily_rep[ph, t + 1] -= COST_PER_UNIT * abs(pos[j] - prev[j])
    # phase average rows
    avg = {k: float(np.nanmean([m[k] for m in phase_pooled[name]])) for k in phase_pooled[name][0]}
    pooled.append(dict(strategy=name, horizon='5d', phase='avg', **avg))
    for y in YEARS:
        if phase_year[name][y]:
            avg_y = {k: float(np.nanmean([m[k] for m in phase_year[name][y]])) for k in phase_year[name][y][0]}
            rows.append(dict(strategy=name, horizon='5d', phase='avg', year=y, **avg_y))
    # daily file: phase 0 and phase average, aligned to the date the return is earned
    daily_out[f'pnl_{name}_phase0'] = daily_rep[0, 1:]
    daily_out[f'pnl_{name}_phaseavg'] = daily_rep.mean(axis=0)[1:]

# Spearman of predicted vs realised 5d return OOS (all rows, overlapping) with NW t
spear = {}
for name, col in [('5d_logistic', 'p_5d'), ('5d_gbm', 'pred_5d')]:
    x = oos.loc[valid5, col].values
    yv = ret5[valid5]
    rho, pval = stats.spearmanr(x, yv)
    zx = (stats.rankdata(x) - stats.rankdata(x).mean()) / stats.rankdata(x).std()
    zy = (stats.rankdata(yv) - stats.rankdata(yv).mean()) / stats.rankdata(yv).std()
    spear[name] = dict(rho=float(rho), p_naive=float(pval), nw_t=float(nw_tstat(zx * zy, 5)), n=int(valid5.sum()))
x1 = oos.loc[valid1, 'p_1d'].values
y1 = oos.loc[valid1, 'next_ret_1d'].values
rho1, _ = stats.spearmanr(x1, y1)
zx = (stats.rankdata(x1) - stats.rankdata(x1).mean()) / stats.rankdata(x1).std()
zy = (stats.rankdata(y1) - stats.rankdata(y1).mean()) / stats.rankdata(y1).std()
spear['ML_1d_logistic'] = dict(rho=float(rho1), p_naive=np.nan, nw_t=float(nw_tstat(zx * zy, 5)), n=int(valid1.sum()))

# ----------------------------------------------------------------------------- verdicts
res = pd.DataFrame(rows)
pool = pd.DataFrame(pooled)
res.to_csv(f'{OUT}/horizon_baseline_results.csv', index=False)
pool.to_csv(f'{OUT}/horizon_baseline_pooled.csv', index=False)
daily_out.to_csv(f'{OUT}/horizon_baseline_daily.csv', index=False)

def psh(strategy, phase='all'):
    return float(pool[(pool.strategy == strategy) & (pool.phase == phase)]['sharpe'].iloc[0])

sh_ml, sh_dma, sh_al, sh_int = psh('ML_1d_logistic'), psh('dma200'), psh('always_long'), psh('dma200_AND_ML')
d4_pass = (sh_dma >= sh_ml - 0.1) and (nw_ml_vs_dma < 2.0)

year_sh = res[res.phase.isin(['all', 'avg'])].pivot(index='year', columns='strategy', values='sharpe')
d2 = {}
for name in sig5:
    sh5 = psh(name, 'avg')
    wins = int((year_sh[name] > year_sh['ML_1d_logistic']).sum())
    nyears = int(year_sh[[name, 'ML_1d_logistic']].dropna().shape[0])
    d2[name] = dict(sharpe=sh5, delta=sh5 - sh_ml, wins=wins, nyears=nyears,
                    passed=(sh5 >= sh_ml + 0.2) and (wins >= 5))

# ----------------------------------------------------------------------------- summary
def fmt_pool(p):
    cols = ['strategy', 'phase', 'n', 'hit_rate', 'in_market', 'ann_return', 'sharpe', 'max_dd', 'trades_per_year']
    q = p[cols].copy()
    for c in ['hit_rate', 'in_market']:
        q[c] = q[c].map(lambda v: f'{v:.3f}')
    for c in ['ann_return', 'max_dd']:
        q[c] = q[c].map(lambda v: f'{100*v:.1f}%')
    q['sharpe'] = q['sharpe'].map(lambda v: f'{v:.2f}')
    q['trades_per_year'] = q['trades_per_year'].map(lambda v: f'{v:.0f}')
    return q.to_string(index=False)

L = []
L.append('# SPY horizon and dumb-baseline study (pre-registered)\n')
L.append(f'OOS rows {n_oos} ({oos.date.min().date()} to {oos.date.max().date()}), walk-forward yearly refit, expanding window.')
L.append(f'Logistic features kept {len(feats_1d)} (stale dropped: {[f for f,_ in drop1]}). GBM 5d features kept {len(feats_5d)} (stale dropped: {[f for f,_ in drop5]}).')
L.append(f'Chosen C per year (1d, 5d): {chosen_c}')
L.append('Cost 0.25 bp per round trip (0.125 bp per unit position change). next_ret_5d = SUM of next 5 daily simple returns.\n')
L.append('## Pooled metrics (daily strategies: n = days; 5d: n = non-overlapping weeks per phase)\n')
L.append('```\n' + fmt_pool(pool) + '\n```\n')
L.append('## Per-year Sharpe (5d rows are the phase average)\n')
L.append('```\n' + year_sh.round(2).to_string() + '\n```\n')
L.append('## Paired tests\n')
L.append(f'- NW(5) t of daily P&L, ML-1d minus 200dma: {nw_ml_vs_dma:.2f} (mean diff {diff.mean()*1e4:.2f} bp/day)')
L.append(f'- NW(5) t of daily P&L, ML-1d minus always-long: {nw_ml_vs_always:.2f}')
for k, v in spear.items():
    L.append(f'- Spearman predicted vs realised ({k}): rho {v["rho"]:.4f}, NW(5) t {v["nw_t"]:.2f}, n {v["n"]}')
L.append('')
L.append('## D4 verdict: 200dma dumb baseline vs ML next-day model\n')
L.append(f'Rule: PASS if Sharpe(200dma) >= Sharpe(ML-1d) - 0.1 AND NW t(ML minus 200dma) < 2.0.')
L.append(f'Sharpe(200dma) {sh_dma:.2f} vs Sharpe(ML-1d) {sh_ml:.2f} (always-long {sh_al:.2f}, intersection {sh_int:.2f}); NW t {nw_ml_vs_dma:.2f}.')
L.append(f'**D4 {"PASS" if d4_pass else "FAIL"}**: the 200dma rule {"matches or beats" if sh_dma >= sh_ml - 0.1 else "does not match"} the ML model out of sample; '
         f'the ML-minus-200dma difference is {"not" if nw_ml_vs_dma < 2.0 else ""} statistically distinguishable from zero.\n')
L.append('## D2 verdict: 5-day horizon vs next-day direction\n')
L.append('Rule: PASS if phase-averaged pooled Sharpe(5d) >= Sharpe(ML-1d) + 0.2 AND per-year Sharpe higher in >= 5 of 8 years.')
for k, v in d2.items():
    L.append(f'- {k}: pooled Sharpe {v["sharpe"]:.2f} (delta {v["delta"]:+.2f} vs ML-1d), higher in {v["wins"]} of {v["nyears"]} years -> **{"PASS" if v["passed"] else "FAIL"}**')
d2_any = any(v['passed'] for v in d2.values())
L.append(f'\n**D2 {"PASS" if d2_any else "FAIL"}** overall ({"at least one" if d2_any else "neither"} 5d model clears both conditions).\n')
with open(f'{OUT}/horizon_baseline_summary.md', 'w') as fh:
    fh.write('\n'.join(L))
print('\n'.join(L))
