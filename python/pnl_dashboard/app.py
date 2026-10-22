#!/usr/bin/env python3
"""
pnl_dashboard/app.py
====================
Browser dashboard for "how is each strategy doing" on the shared Alpaca
paper account. Reuses pnlLedger.py's own account-reconciled P&L engine
(the source of truth already posted to #test-env daily) instead of
re-deriving anything - this process only READS the broker via the same
incremental fills cache pnlLedger.py already maintains, and never writes
pnl_ledger*.csv itself (pnlLedger.py's own scheduled run still owns those).

Architecture: one in-process background refresh loop (no websocket poller
needed - this is a cron-cadence/REST data source, not tick-level), recomputing
the full per-strategy snapshot every REFRESH_SEC and caching it in memory.
Flask routes only ever read the cached snapshot - never block on a live
Alpaca call - so N browser tabs polling /api/live costs nothing extra.

Routes
  GET /            dashboard page (navy theme)
  GET /api/live    {generated_at, age_s, stale, account, strategies[], daily[]}

Run
  venv/bin/python3 python/pnl_dashboard/app.py --port 8901
"""
import os
import sys
import csv
import time
import threading
import argparse
import datetime as dt

from flask import Flask, jsonify, render_template_string

HERE = os.path.dirname(os.path.abspath(__file__))
PY_DIR = os.path.dirname(HERE)
sys.path.insert(0, PY_DIR)

import pnlLedger as pl  # noqa: E402

REFRESH_SEC = 90
STALE_AFTER_SEC = 240  # badge flips to "stale" if the loop stalls this long

app = Flask(__name__)

_lock = threading.Lock()
_state = {"generated_at": None, "ts": 0.0, "account": None, "strategies": [],
          "daily": [], "error": None}


# Market-making bots killed Oct 19 2026 (losing ~$0.27/fill over 6.8k fills),
# and crypto grid bots killed Oct 22 2026 (study 18 backtest: structurally
# negative after fees across all three coins - fees ate 46-74% of gross
# round-trip income, net -$182/-$565/-$15 vs vol-matched buy&hold over 12mo)
# - excluded from this live dashboard. pnlLedger.py's own CSVs/history still
# carry their final numbers untouched; this is a display-only filter.
DEAD_STRATEGIES = {"MM SOXL", "MM TQQQ (RL)", "MM GME", "Grid BTC", "Grid ETH", "Grid SOL"}


def _order():
    order = [s for s, _ in pl.STRATEGIES.values()] + [pl.VOL_BOOK[0]]
    order = [s for s in order if s not in DEAD_STRATEGIES]
    return list(dict.fromkeys(order)) + [pl.LEGACY]


def compute_snapshot():
    """One full pass of pnlLedger's engine, in-memory only (no file writes,
    no Discord post, no R chart subprocess) - mirrors pnlLedger.main()'s
    table/daily_rows loop exactly so the dashboard never drifts from the
    canonical ledger's numbers."""
    headers = pl.alpaca_headers()
    fills, n_new = pl.refresh_fills(headers)
    positions = pl.get_positions(headers)
    account = pl.get_account(headers)
    today = dt.date.today().isoformat()

    books = pl.build_books(fills)
    pl.reconcile_to_broker(books, positions)

    table, daily_rows = [], []
    for strat in _order():
        syms = books.get(strat, {})
        if not syms:
            continue
        launch = pl._launch_of(strat)
        real_books_probe = [b for b in syms.values() if b.first]
        if not real_books_probe:
            continue
        start = launch or min(b.first for b in real_books_probe)[:10]
        closes = {s: pl.daily_closes(s, start, headers) for s in syms} if strat != pl.LEGACY else {}
        realized = sum(b.realized for b in syms.values())
        unreal, inv = 0.0, []
        for s, b in syms.items():
            if abs(b.qty) > 1e-9:
                mark = pl._close_on_or_before(closes.get(s, {}), today)
                if mark is None:
                    pos = positions.get(s) or positions.get(s.replace("/", ""))
                    mark = float(pos["current_price"]) * pl._mult(s) if pos else None
                if mark is not None:
                    unreal += b.qty * (mark - b.avg)
                inv.append(f"{b.qty:.4g} {s}")
        fills_n = sum(b.fills for b in syms.values())
        traded = sum(b.bought + b.sold for b in syms.values())
        real_books = [b for b in syms.values() if b.first]
        first = min(b.first for b in real_books)[:10]
        last = max(b.last for b in real_books)[:10]
        days = max(1, (dt.date.fromisoformat(today) - dt.date.fromisoformat(first)).days + 1)
        total = realized + unreal
        mdd = 0.0
        if strat != pl.LEGACY:
            ser = pl.mtm_series(syms, closes, start, today)
            mdd = pl.max_drawdown([v[3] for v in ser])
            for day, cr, ur, tot in ser:
                daily_rows.append({"date": day, "strategy": strat, "realized": round(cr, 2),
                                    "unrealized": round(ur, 2), "total": round(tot, 2)})
        fees = sum(b.fees for b in syms.values())
        table.append({
            "strategy": strat, "first_fill": first, "last_fill": last, "days": days,
            "fills": fills_n, "traded_notional": round(traded, 2),
            "realized": round(realized, 2), "unrealized": round(unreal, 2),
            "total": round(total, 2), "fees": round(fees, 2), "max_dd": round(mdd, 2),
            "per_fill": round(total / fills_n, 4) if fills_n else 0.0,
            "inventory": "; ".join(inv) or "flat",
            "status": pl.status_for(total, mdd, days, fills_n),
            "is_legacy": strat == pl.LEGACY,
        })

    acct_out = {
        "equity": float(account.get("equity", 0) or 0),
        "cash": float(account.get("cash", 0) or 0),
        "buying_power": float(account.get("buying_power", 0) or 0),
    }
    return table, daily_rows, acct_out, n_new


INTRADAY_CSV = os.path.join(pl.OUT, "pnl_dashboard_intraday.csv")
INTRADAY_FIELDS = ["ts", "date", "strategy", "total"]


def _read_intraday_rows():
    if not os.path.exists(INTRADAY_CSV):
        return []
    with open(INTRADAY_CSV) as f:
        return [{"ts": r["ts"], "date": r["date"], "strategy": r["strategy"], "total": float(r["total"])}
                for r in csv.DictReader(f)]


def _append_intraday(table):
    """Self-sample today's cumulative total per strategy at refresh cadence.
    pnlLedger itself only has EOD granularity (one row/day) - there was never
    a historical intraday number to backfill, so this just starts building
    real history going forward, same pattern as any live-only metric with no
    tick/fill-replay infra behind it. Old days are dropped on every write so
    the file stays small (one trading day's worth of REFRESH_SEC-spaced rows)."""
    now = dt.datetime.now()
    today = now.date().isoformat()
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    rows = [r for r in _read_intraday_rows() if r["date"] == today]
    for t in table:
        if t["is_legacy"]:
            continue
        rows.append({"ts": ts, "date": today, "strategy": t["strategy"], "total": t["total"]})
    with open(INTRADAY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INTRADAY_FIELDS)
        w.writeheader()
        w.writerows(rows)


def _refresh_loop():
    while True:
        try:
            table, daily_rows, acct, n_new = compute_snapshot()
            with _lock:
                _state.update(generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               ts=time.time(), account=acct, strategies=table,
                               daily=daily_rows, error=None)
            _append_intraday(table)
            print(f"[pnl_dashboard] refreshed ({n_new} new activities, "
                  f"{len(table)} strategies)", file=sys.stderr)
        except Exception as e:
            with _lock:
                _state["error"] = str(e)
            print(f"[pnl_dashboard] refresh failed: {e}", file=sys.stderr)
        time.sleep(REFRESH_SEC)


@app.route("/api/intraday")
def api_intraday():
    today = dt.date.today().isoformat()
    points = [r for r in _read_intraday_rows() if r["date"] == today]
    return jsonify({"date": today, "points": points})


@app.route("/api/live")
def api_live():
    with _lock:
        snap = dict(_state)
    age = time.time() - snap["ts"] if snap["ts"] else None
    live = [t for t in snap["strategies"] if not t["is_legacy"]]
    legacy = next((t for t in snap["strategies"] if t["is_legacy"]), None)
    return jsonify({
        "ok": snap["ts"] > 0,
        "generated_at": snap["generated_at"],
        "age_s": age,
        "stale": age is not None and age > STALE_AFTER_SEC,
        "error": snap["error"],
        "account": snap["account"],
        "live_total": {
            "realized": round(sum(t["realized"] for t in live), 2),
            "unrealized": round(sum(t["unrealized"] for t in live), 2),
            "total": round(sum(t["total"] for t in live), 2),
            "fees": round(sum(t["fees"] for t in live), 2),
            "fills": sum(t["fills"] for t in live),
        },
        "strategies": live,
        "legacy": legacy,
        "daily": snap["daily"],
    })


PAGE = r"""
<!doctype html><html><head><meta charset="utf-8">
<title>Alpaca P&L by Strategy</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<style>
  :root { --bg:#02233F; --grid:#274066; --txt:#ffffff; --pos:#2E9E4E; --neg:#D9453D;
          --panel:#031b33; --chip:#0a2d4e; }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--txt); font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         margin:0; padding:16px; }
  h1 { font-size:20px; margin:0 0 4px 0; }
  .sub { color:#cfe3ff; font-size:12px; margin-bottom:14px; }
  .badge { display:inline-block; padding:2px 9px; border-radius:10px; font-size:11px; font-weight:bold; margin-left:8px; }
  .badge.live { background:#1f4d2e; color:#6FE08A; }
  .badge.stale { background:#4d1f1f; color:#ff9b9b; }
  .top { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:16px; }
  .stat { background:var(--chip); border:1px solid var(--grid); border-radius:8px; padding:10px 16px; min-width:150px; }
  .stat .lbl { font-size:11px; color:#9fc2e8; text-transform:uppercase; letter-spacing:.04em; }
  .stat .val { font-size:22px; font-weight:700; margin-top:2px; }
  .pos { color: var(--pos); } .neg { color: var(--neg); }
  .panel { background: var(--panel); border:1px solid var(--grid); border-radius:10px; padding:16px; margin-bottom:18px; }
  .panel h2 { font-size:14px; margin:0 0 10px 0; color:#cfe3ff; }
  canvas { max-height: 360px; }
  .cards { display:flex; flex-wrap:wrap; gap:12px; }
  .card { flex:1 1 220px; min-width:220px; background:var(--chip); border:1px solid var(--grid);
          border-radius:8px; padding:12px 14px; }
  .card .name { font-weight:700; font-size:14px; display:flex; align-items:center; gap:6px; }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
  .dot.green{background:#2E9E4E} .dot.amber{background:#E0A02E} .dot.red{background:#D9453D}
  .dot.warmup{background:#5A7290} .dot.idle{background:#5A7290}
  .card .total { font-size:20px; font-weight:800; margin:6px 0 4px 0; }
  .card .row { font-size:11.5px; color:#b9d2ef; display:flex; justify-content:space-between; }
  .card .inv { font-size:10.5px; color:#8fb0d6; margin-top:6px; word-break:break-word; }
  table { width:100%; border-collapse: collapse; font-size:12.5px; }
  th, td { text-align:right; padding:6px 8px; border-bottom:1px solid var(--grid); white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  th { color:#9fc2e8; font-weight:600; font-size:11px; text-transform:uppercase; }
  tr.legacy td { color:#8398b5; font-style:italic; }
  .legend-note { font-size:11px; color:#8398b5; margin-top:8px; }
  .tf-toggle { display:inline-flex; gap:0; margin-left:12px; border:1px solid var(--grid); border-radius:6px; overflow:hidden; vertical-align:middle; }
  .tf-toggle button { background:var(--chip); color:#9fc2e8; border:none; padding:4px 12px; font-size:12px; cursor:pointer; }
  .tf-toggle button.active { background:#4C9BE0; color:#02233F; font-weight:700; }
  .tf-toggle button:hover:not(.active) { background:#0f3a63; }
</style></head>
<body>
<h1>Alpaca P&amp;L by Strategy <span id="badge" class="badge">...</span></h1>
<div class="sub" id="asof">loading...</div>

<div class="top" id="topcards"></div>

<div class="panel">
  <h2>Cumulative P&amp;L by strategy
    <span class="tf-toggle">
      <button id="tf-full" class="active" onclick="setView('full')">Full view</button>
      <button id="tf-today" onclick="setView('today')">Today (intraday)</button>
    </span>
  </h2>
  <canvas id="chart"></canvas>
  <div class="legend-note" id="legend-note">Click a legend entry to hide/show that strategy. Legacy/manual (pre-launch fills) is excluded from the chart - its P&amp;L is a different order of magnitude and isn't a live strategy.</div>
</div>

<div class="panel">
  <h2>Strategy snapshot</h2>
  <div class="cards" id="cards"></div>
</div>


<div class="panel">
  <h2>Detail table</h2>
  <div style="overflow-x:auto">
  <table id="tbl">
    <thead><tr><th>strategy</th><th>status</th><th>days</th><th>fills</th><th>realized</th>
    <th>fees</th><th>unrealized</th><th>total</th><th>max DD</th><th>$/fill</th><th>inventory</th></tr></thead>
    <tbody></tbody>
  </table>
  </div>
</div>

<script>
const PALETTE = ["#4C9BE0","#2E9E4E","#C9A6FF","#E0A02E","#D9453D","#2E7BE0","#7B4CE0","#6FE08A","#F2C94C"];
let chart = null;
let currentView = 'full';
let lastDaily = [];
let lastStrategies = [];
let lastIntraday = { date: null, points: [] };
const FULL_NOTE = "Click a legend entry to hide/show that strategy. Legacy/manual (pre-launch fills) is excluded from the chart - its P&amp;L is a different order of magnitude and isn't a live strategy.";

function money(x, dp) {
  dp = dp === undefined ? 2 : dp;
  const s = Math.abs(x) >= 1000 ? x.toLocaleString(undefined, {maximumFractionDigits:0, minimumFractionDigits:0})
                                 : x.toLocaleString(undefined, {maximumFractionDigits:dp, minimumFractionDigits:dp});
  return (x >= 0 ? "+" : "") + s;
}
function signClass(x) { return x >= 0 ? "pos" : "neg"; }

function renderChart(datasets, timeUnit, displayFormats) {
  const timeCfg = { unit: timeUnit };
  if (displayFormats) timeCfg.displayFormats = displayFormats;
  const cfg = {
    type: 'line',
    data: { datasets },
    options: {
      animation: false, responsive: true,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      scales: {
        x: { type: 'time', time: timeCfg, grid: { color: '#274066' }, ticks: { color: '#cfe3ff' } },
        y: { grid: { color: '#274066' }, ticks: { color: '#cfe3ff', callback: v => '$' + v } },
      },
      plugins: {
        legend: { labels: { color: '#ffffff' } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: $${c.parsed.y.toFixed(2)}` } },
      },
    },
  };
  if (chart) { chart.data = cfg.data; chart.options = cfg.options; chart.update('none'); }
  else { chart = new Chart(document.getElementById('chart'), cfg); }
}

function buildChartFull(daily, strategies) {
  const byStrat = {};
  daily.forEach(r => { (byStrat[r.strategy] = byStrat[r.strategy] || []).push({x: r.date, y: r.total}); });
  const order = strategies.map(s => s.strategy);
  const live = order.filter(s => byStrat[s]);
  const datasets = live.map((s, i) => ({
    label: s, data: byStrat[s], borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length], borderWidth: 2, pointRadius: 0,
    tension: 0.15, spanGaps: true, order: 1,
  }));

  // combined TOTAL line: sum of every live strategy's cumulative total per
  // date (a date before a strategy's launch contributes 0, not missing -
  // the strategy genuinely hadn't made/lost anything yet).
  const sumByDate = {};
  live.forEach(s => { byStrat[s].forEach(pt => { sumByDate[pt.x] = (sumByDate[pt.x] || 0) + pt.y; }); });
  const totalData = Object.keys(sumByDate).sort().map(d => ({x: d, y: Math.round(sumByDate[d] * 100) / 100}));
  datasets.unshift({
    label: 'TOTAL (all live)', data: totalData, borderColor: '#ffffff',
    backgroundColor: '#ffffff', borderWidth: 3, pointRadius: 0, tension: 0.15,
    spanGaps: true, order: 0,
  });

  renderChart(datasets, 'day');
  document.getElementById('legend-note').innerHTML = FULL_NOTE;
}

function buildChartIntraday(intraday, strategies) {
  const points = (intraday && intraday.points) || [];
  const byStrat = {};
  points.forEach(r => { (byStrat[r.strategy] = byStrat[r.strategy] || []).push({x: r.ts, y: r.total}); });
  const order = strategies.map(s => s.strategy);
  const live = order.filter(s => byStrat[s]);
  const datasets = live.map((s, i) => ({
    label: s, data: byStrat[s], borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length], borderWidth: 2, pointRadius: 2,
    tension: 0.1, spanGaps: true, order: 1,
  }));

  const sumByTs = {};
  live.forEach(s => { byStrat[s].forEach(pt => { sumByTs[pt.x] = (sumByTs[pt.x] || 0) + pt.y; }); });
  const totalData = Object.keys(sumByTs).sort().map(ts => ({x: ts, y: Math.round(sumByTs[ts] * 100) / 100}));
  datasets.unshift({
    label: 'TOTAL (all live)', data: totalData, borderColor: '#ffffff',
    backgroundColor: '#ffffff', borderWidth: 3, pointRadius: 2, tension: 0.1,
    spanGaps: true, order: 0,
  });

  renderChart(datasets, 'minute', { minute: 'HH:mm' });
  document.getElementById('legend-note').textContent = points.length
    ? `Today (${intraday.date}), self-sampled every ~90s - starts from whenever the dashboard began tracking today, not market open.`
    : 'No intraday samples yet today - the dashboard self-samples every ~90s once it is running, there is no backfill for earlier today.';
}

function updateChart() {
  if (currentView === 'today') buildChartIntraday(lastIntraday, lastStrategies);
  else buildChartFull(lastDaily, lastStrategies);
}

function setView(v) {
  currentView = v;
  document.getElementById('tf-full').classList.toggle('active', v === 'full');
  document.getElementById('tf-today').classList.toggle('active', v === 'today');
  updateChart();
}

function render(d) {
  document.getElementById('badge').className = 'badge ' + (d.stale ? 'stale' : 'live');
  document.getElementById('badge').textContent = d.stale ? 'STALE' : 'LIVE';
  document.getElementById('asof').textContent =
    `as of ${d.generated_at} ET  (refreshed ${Math.round(d.age_s)}s ago)` + (d.error ? `  -  last refresh error: ${d.error}` : '');

  const t = d.live_total;
  const top = [
    {lbl: 'Live total P&L', val: money(t.total), cls: signClass(t.total)},
    {lbl: 'Realized', val: money(t.realized), cls: signClass(t.realized)},
    {lbl: 'Unrealized', val: money(t.unrealized), cls: signClass(t.unrealized)},
    {lbl: 'Fees paid', val: money(t.fees), cls: 'neg'},
    {lbl: 'Fills (all strategies)', val: t.fills.toLocaleString(), cls: ''},
    {lbl: 'Account equity', val: d.account ? '$' + d.account.equity.toLocaleString(undefined,{maximumFractionDigits:0}) : '-', cls: ''},
  ];
  document.getElementById('topcards').innerHTML = top.map(c =>
    `<div class="stat"><div class="lbl">${c.lbl}</div><div class="val ${c.cls}">${c.lbl.includes('equity')||c.lbl.includes('Fills')?c.val:'$'+c.val}</div></div>`
  ).join('');

  document.getElementById('cards').innerHTML = d.strategies.map(s => `
    <div class="card">
      <div class="name"><span class="dot ${s.status}"></span>${s.strategy}</div>
      <div class="total ${signClass(s.total)}">$${money(s.total)}</div>
      <div class="row"><span>realized</span><span class="${signClass(s.realized)}">$${money(s.realized)}</span></div>
      <div class="row"><span>unrealized</span><span class="${signClass(s.unrealized)}">$${money(s.unrealized)}</span></div>
      <div class="row"><span>fees</span><span class="neg">$${money(s.fees)}</span></div>
      <div class="row"><span>max drawdown</span><span class="neg">$${money(s.max_dd)}</span></div>
      <div class="row"><span>fills / days</span><span>${s.fills.toLocaleString()} / ${s.days}</span></div>
      <div class="inv">${s.inventory}</div>
    </div>`).join('');

  const rows = d.strategies.slice();
  const tbody = document.querySelector('#tbl tbody');
  tbody.innerHTML = rows.map(s => `
    <tr class="${s.is_legacy ? 'legacy' : ''}">
      <td>${s.strategy}</td>
      <td>${s.status}</td>
      <td>${s.days}</td>
      <td>${s.fills.toLocaleString()}</td>
      <td class="${signClass(s.realized)}">${money(s.realized)}</td>
      <td class="neg">${money(s.fees)}</td>
      <td class="${signClass(s.unrealized)}">${money(s.unrealized)}</td>
      <td class="${signClass(s.total)}">${money(s.total)}</td>
      <td class="neg">${money(s.max_dd)}</td>
      <td>${s.per_fill.toFixed(3)}</td>
      <td style="text-align:left; max-width:360px; white-space:normal;">${s.inventory}</td>
    </tr>`).join('');

  lastDaily = d.daily;
  lastStrategies = d.strategies;
  updateChart();
}

async function poll() {
  try {
    const [liveResp, intraResp] = await Promise.all([fetch('/api/live'), fetch('/api/intraday')]);
    const d = await liveResp.json();
    lastIntraday = await intraResp.json();
    if (d.ok) render(d); else if (currentView === 'today') updateChart();
  } catch (e) { console.error(e); }
}
poll();
setInterval(poll, 20000);
</script>
</body></html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()
    app.run(host=args.host, port=args.port, threaded=True)
