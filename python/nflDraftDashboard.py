#!/usr/bin/env python3
"""
nflDraftDashboard.py

Live draft-day VISUAL dashboard. Runs as a local Dash/Plotly app - this
file does NOT reimplement any of the live-draft plumbing. It imports and
reuses everything from nflDraftWatch.py / nflMockDraftLog.py (Sleeper
polling, marginal-VORP recommendation engine, roster tracking, hard-exclude
logic, ADP/weekly-volatility context columns) and just adds a browser UI on
top. Sleeper has no draft webhooks, so this polls the same way the CLI
watcher does - dcc.Interval on a timer, default 8s.

USAGE:
  venv/bin/python3 python/nflDraftDashboard.py <draft_id> <my_slot> [--poll 8] [--port 8877]

Then open http://127.0.0.1:8877 in a browser on the SAME machine you ran
this from (no Pi hosting/networking setup needed - this is meant to run on
your laptop during the draft, right next to the Sleeper tab).

draft_id: bare numeric Sleeper draft ID, or paste the full draft URL
          (query strings/fragments are stripped automatically, same as
          nflDraftWatch.py).
my_slot:  your draft-order position for round 1 (e.g. "I have pick 10" -> 10).

LAYOUT:
  1. Status banner - your turn / N picks away, roster needs, last poll time
  2. My Roster table | Best Available table (position-filterable, sortable by
     any column asc/desc - recommend()'s marginal-VORP ranking, same
     hard-exclude-filled-non-FLEX-position logic as the CLI watcher)
  3. Scoring volatility scatter (left) + NGS advanced-stats tabs (right),
     side by side on the same row. Volatility: x = stdev of 2025 weekly pts,
     y = mean weekly pts, bubble size = 2025 season TOTAL pts, color =
     Sharpe (mean/sd, computed live). Position + FLEX filter checklist.
     NGS: same metrics as /nfl wrstats|rbstats|qbstats|testats in the
     Discord bot (separation/YAC, RYOE/efficiency, CPOE/aggressiveness),
     sourced from the ngs_*_export.csv files those R scripts also write.
     Both live-filtered - drafted players drop off every poll cycle.
  4. Aging curve panel (population-level curve from nflAgingCurves.R) with
     the top-5 still-available players per position placed on the curve at
     their real age (star markers) - updates live as players get drafted.
  5. Breakout candidates panel - is_breakout-flagged players (from
     buildNflBreakoutScore.py) still on the board, sorted by breakout_z.

Navy theme matches the existing NFL R plot house style (nflWrStats.R /
nflQbStats.R / nflRbStats.R / nflTeStats.R / buildNflDraftBoard.py):
  BG=#02233F, GRID=#274066, TXT=white
  Position colors: QB=#7B4CE0 RB=#2E9E4E WR=#2E7BE0 TE=#E0A02E
"""
import argparse
import os
import re
import sys
import unicodedata
from datetime import datetime

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Dash, dcc, html, dash_table, Input, Output, State, ctx, no_update

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nflMockDraftLog import (
    FLEX_ELIGIBLE, STARTERS, fetch_sleeper_draft, load_id_bridge,
    load_vorp_board, match_player, score_starting_lineup,
)
from nflDraftWatch import (
    ADP_CAP, _extract_draft_id, fetch_picks_tolerant, load_draft_context,
    picks_until_my_turn, predict_next_slot, recommend, roster_needs_str,
)

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
NFL_OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl")  # NGS export CSVs live here

# ── Navy theme (matches nflWrStats.R / buildNflDraftBoard.py) ──────────────
BG = "#02233F"
PANEL = "#0a2840"
GRID = "#274066"
TXT = "#e8eaf6"
TXT_MUTED = "#7fa8c4"
POS_COLORS = {"QB": "#7B4CE0", "RB": "#2E9E4E", "WR": "#2E7BE0", "TE": "#E0A02E"}
POS_SYMBOLS = {"QB": "diamond", "RB": "square", "WR": "circle", "TE": "triangle-up"}
ACCENT_GOOD = "#26a69a"
ACCENT_BAD = "#FF4444"
ACCENT_WARN = "#FF8C00"
GOLD = "#FFD700"

SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    """Same convention used throughout the repo (buildNflDraftBoard.py,
    nflDraftWatch.py) - strip accents/suffixes/punctuation for cross-source
    name joins (Sleeper full_name vs nflverse player_display_name vs NGS
    export 'name')."""
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIX_RE.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def navy_fig_layout(fig, title, subtitle=None, x_title=None, y_title=None):
    fig.update_layout(
        title=dict(
            text=f"{title}<br><span style='font-size:12px;color:{TXT_MUTED}'>{subtitle}</span>" if subtitle else title,
            font=dict(color=TXT, size=16),
        ),
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font=dict(color=TXT),
        xaxis=dict(title=x_title, gridcolor=GRID, zerolinecolor=GRID, color=TXT_MUTED),
        yaxis=dict(title=y_title, gridcolor=GRID, zerolinecolor=GRID, color=TXT_MUTED),
        legend=dict(bgcolor=PANEL, bordercolor=GRID, borderwidth=1, font=dict(color=TXT)),
        margin=dict(l=60, r=30, t=70, b=50),
    )
    return fig


DARK_TABLE_STYLE = dict(
    style_header={"backgroundColor": PANEL, "color": TXT, "fontWeight": "bold", "border": f"1px solid {GRID}"},
    style_cell={"backgroundColor": BG, "color": TXT, "border": f"1px solid {GRID}",
                "fontFamily": "monospace", "fontSize": 12, "padding": "4px 8px"},
    style_data_conditional=[],
    style_table={"overflowX": "auto"},
)


# ── Static / rarely-changing data loaded once at startup ───────────────────
def load_ngs_exports():
    """Each NGS export is independently optional - a missing file (R script
    never run) degrades that tab to 'no data' rather than crashing the app."""
    exports = {}
    for pos, fname in (("WR", "ngs_wr_export.csv"), ("TE", "ngs_te_export.csv"),
                        ("RB", "ngs_rb_export.csv"), ("QB", "ngs_qb_export.csv")):
        path = os.path.join(NFL_OUT_DIR, fname)
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["norm_name"] = df["name"].map(norm_name)
            exports[pos] = df
        else:
            exports[pos] = None
            print(f"NOTE: {fname} not found - run r/nfl{pos.title()}Stats.R first. "
                  f"{pos} NGS tab will show 'no data'.")
    return exports


def load_breakout_candidates():
    path = os.path.join(OUT_DIR, "breakout_candidates_2026.csv")
    if not os.path.exists(path):
        print("NOTE: breakout_candidates_2026.csv not found - breakout panel will be empty.")
        return pd.DataFrame(columns=["norm_name", "position", "player_display_name", "breakout_z"])
    cand = pd.read_csv(path)
    cand["norm_name"] = cand["player_display_name"].map(norm_name)
    return cand[["norm_name", "position", "player_display_name", "breakout_z"]]


def load_kdst_volatility():
    """K and DEF/ST are out of scope for the VORP/ADP moneyball pipeline
    (see buildNflVorp.py docstring - skill positions only), so there's no
    live-draft roster tracking for them here. This loads the standalone 2025
    season volatility CSVs (fetchNflKickerVolatility.R / fetchNflDstVolatility.R)
    for the mean-vs-volatility chart only - season-long, not filtered by
    who's still on the board (no VORP board join available to know that)."""
    out = {}
    k_path = os.path.join(OUT_DIR, "weekly_volatility_kickers_2025.csv")
    if os.path.exists(k_path):
        df = pd.read_csv(k_path)
        df = df.rename(columns={"player_display_name": "full_name"})
        df["position"] = "K"
        out["K"] = df
    else:
        out["K"] = None
        print("NOTE: weekly_volatility_kickers_2025.csv not found - run r/fetchNflKickerVolatility.R first.")
    dst_path = os.path.join(OUT_DIR, "weekly_volatility_dst_2025.csv")
    if os.path.exists(dst_path):
        df = pd.read_csv(dst_path)
        df = df.rename(columns={"team": "full_name"})
        df["position"] = "DEF"
        out["DEF"] = df
    else:
        out["DEF"] = None
        print("NOTE: weekly_volatility_dst_2025.csv not found - run r/fetchNflDstVolatility.R first.")
    return out


def load_aging_curves():
    curves_path = os.path.join(OUT_DIR, "aging_curves.csv")
    summary_path = os.path.join(OUT_DIR, "aging_curve_summary.csv")
    if not (os.path.exists(curves_path) and os.path.exists(summary_path)):
        print("NOTE: aging_curves.csv / aging_curve_summary.csv not found - "
              "run r/nflAgingCurves.R first. Aging panel will be empty.")
        return None, None
    return pd.read_csv(curves_path), pd.read_csv(summary_path)


def load_player_ages():
    """Latest-season age per player from nfl_training_panel.csv (built by
    mergeNflPhase1Features.R / fetchNflCareerData.R pipeline) - used to place
    still-available players on the population-level aging curve. Optional:
    a missing file just means no name markers get added to that panel."""
    path = os.path.join(OUT_DIR, "nfl_training_panel.csv")
    if not os.path.exists(path):
        print("NOTE: nfl_training_panel.csv not found - aging curve will show "
              "the population curve only, no player name markers.")
        return None
    df = pd.read_csv(path, usecols=["player_display_name", "position", "season", "age"])
    latest = df.sort_values("season").groupby(["player_display_name", "position"]).last().reset_index()
    latest["norm_name"] = latest["player_display_name"].map(norm_name)
    return latest[["norm_name", "position", "age"]]


AGING_POSITIONS = ["QB", "RB", "WR", "TE"]


def build_aging_curve_figure(curves, summary, available_df=None, age_lookup=None, top_n=10, positions=None):
    """positions=None (or omitted) means show all 4 - matches original behavior.
    Pass a subset (e.g. ["RB", "WR"]) to draw curves for ONLY those positions;
    positions left out are fully removed from the chart (no line, no markers) -
    their top-N available players are surfaced instead via
    build_dropped_players_panel() so nothing about them silently disappears."""
    shown_positions = AGING_POSITIONS if positions is None else [p for p in AGING_POSITIONS if p in positions]
    fig = go.Figure()
    if curves is None:
        navy_fig_layout(fig, "Aging Curves", "run r/nflAgingCurves.R to populate this panel")
        return fig
    for pos in shown_positions:
        sub = curves[curves["position"] == pos]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["age"], y=sub["combined_index"], mode="lines", name=pos,
            line=dict(color=POS_COLORS.get(pos, TXT), width=2.5),
        ))
        if summary is not None:
            peak_row = summary[summary["position"] == pos]
            if not peak_row.empty:
                fig.add_trace(go.Scatter(
                    x=peak_row["peak_age"], y=[100], mode="markers", showlegend=False,
                    marker=dict(color=POS_COLORS.get(pos, TXT), size=10, symbol="diamond"),
                    hovertext=f"{pos} peak age {int(peak_row['peak_age'].iloc[0])}",
                ))

        # Top-N still-available players at this position, placed on the curve
        # at their real age (interpolated against the population curve) -
        # gives a "where do my actual options sit" view instead of just the
        # abstract population trend. Available_df/age_lookup are optional -
        # panel degrades to the plain population curve if either is missing.
        if available_df is not None and age_lookup is not None:
            pos_avail = available_df[available_df["position"] == pos].sort_values("vorp", ascending=False).head(top_n)
            if not pos_avail.empty:
                merged = pos_avail.merge(age_lookup[age_lookup["position"] == pos][["norm_name", "age"]],
                                          on="norm_name", how="left").dropna(subset=["age"])
                if not merged.empty:
                    curve_x = sub["age"].values
                    curve_y = sub["combined_index"].values
                    order = curve_x.argsort()
                    merged["y_on_curve"] = pd.Series(
                        np.interp(merged["age"], curve_x[order], curve_y[order])
                    ).values
                    fig.add_trace(go.Scatter(
                        x=merged["age"], y=merged["y_on_curve"], mode="markers+text",
                        text=merged["full_name"].str.split().str[-1], textposition="top center",
                        textfont=dict(size=9, color=TXT),
                        marker=dict(color=POS_COLORS.get(pos, TXT), size=11, symbol="star",
                                    line=dict(width=1.5, color=TXT)),
                        showlegend=False,
                        customdata=merged[["full_name", "vorp", "age"]],
                        hovertemplate="<b>%{customdata[0]}</b> (" + pos + ")<br>Age %{customdata[2]:.1f} | "
                                      "VORP %{customdata[1]:.1f}<extra></extra>",
                    ))
    fig.add_hline(y=100, line_dash="dash", line_color=TXT_MUTED)
    subtitle = "Room 40 scoring | delta-method aging curve, indexed to peak age = 100"
    if available_df is not None:
        subtitle += f" | \u2605 = top {top_n} available per position (by VORP)"
    navy_fig_layout(
        fig, "Fantasy Career Value by Age", subtitle,
        x_title="Age (as of Sept 1)", y_title="Combined Season-Value Index",
    )
    return fig


NGS_METRIC_MAP = {
    "WR": dict(x="avg_sep", y="avg_yac_oe", size="sz_targets", color="rec_epa",
               x_title="Avg Separation at Catch (yds)", y_title="Avg YAC Over Expected (yds)",
               subtitle="Separation vs YAC Over Expected | bubble = targets | color = Rec EPA"),
    "TE": dict(x="avg_sep", y="avg_yac_oe", size="sz_targets", color="rec_epa",
               x_title="Avg Separation at Catch (yds)", y_title="Avg YAC Over Expected (yds)",
               subtitle="Separation vs YAC Over Expected | bubble = targets | color = Rec EPA"),
    "RB": dict(x="efficiency", y="ryoe_per_att", size="att", color="ryoe_per_att",
               x_title="NGS Efficiency Score", y_title="Rush Yards Over Expected per Carry",
               subtitle="NGS Efficiency vs Rush Yards Over Expected per Carry | bubble = carries"),
    "QB": dict(x="aggressiveness", y="cpoe_ngs", size="att", color="pass_epa",
               x_title="Aggressiveness % (tight-window throws)", y_title="CPOE (Completion % Above Expected)",
               subtitle="Aggressiveness vs CPOE | bubble = attempts | color = Pass EPA"),
}


def build_ngs_figure(pos, ngs_df, available_norm_names):
    spec = NGS_METRIC_MAP[pos]
    fig = go.Figure()
    if ngs_df is None:
        navy_fig_layout(fig, f"{pos} Advanced Stats (NGS)", "no data - run the matching R script first")
        return fig
    df = ngs_df[ngs_df["norm_name"].isin(available_norm_names)].copy()
    if df.empty:
        navy_fig_layout(fig, f"{pos} Advanced Stats (NGS)", "no available players match this filter")
        return fig
    fig.add_trace(go.Scatter(
        x=df[spec["x"]], y=df[spec["y"]], mode="markers+text",
        text=df["name"].str.split().str[-1], textposition="top center",
        textfont=dict(size=9, color=TXT_MUTED),
        marker=dict(
            size=df[spec["size"]], sizemode="area",
            sizeref=2. * df[spec["size"]].max() / (40. ** 2) if df[spec["size"]].max() else 1,
            sizemin=4, color=df[spec["color"]], colorscale=[[0, ACCENT_BAD], [0.5, TXT], [1, ACCENT_GOOD]],
            colorbar=dict(title=dict(text=spec["color"], font=dict(color=TXT_MUTED)), tickfont=dict(color=TXT_MUTED)),
            line=dict(width=1, color=GRID),
        ),
        customdata=df[["name"]],
        hovertemplate="%{customdata[0]}<br>%{xaxis.title.text}: %{x:.2f}<br>%{yaxis.title.text}: %{y:.2f}<extra></extra>",
    ))
    fig.add_vline(x=df[spec["x"]].mean(), line_dash="dash", line_color=GRID)
    fig.add_hline(y=0 if df[spec["y"]].min() < 0 else df[spec["y"]].mean(), line_dash="dash", line_color=GRID)
    navy_fig_layout(fig, f"{pos} Advanced Stats (NGS, {len(df)} available)", spec["subtitle"],
                     x_title=spec["x_title"], y_title=spec["y_title"])
    return fig


def build_volatility_figure(avail_df, positions):
    fig = go.Figure()
    df = avail_df[avail_df["position"].isin(positions)].copy()
    df = df[df["pts_mean_2025"].notna() & df["pts_sd_2025"].notna()]
    if df.empty:
        navy_fig_layout(fig, "2025 Weekly Scoring: Volatility vs Mean",
                         "no available players with 2025 weekly data match this filter")
        return fig

    # Sharpe = mean / sd of weekly points (James's exact definition - recomputed
    # here rather than trusting the pts_sharpe_2025 context column, so the color
    # always matches what's labeled). Guard divide-by-zero for a dead-flat scorer.
    sd_safe = df["pts_sd_2025"].replace(0, pd.NA)
    df["sharpe_calc"] = pd.to_numeric(df["pts_mean_2025"], errors="coerce") / pd.to_numeric(sd_safe, errors="coerce")

    # Total fantasy points scored last season (2025) - lag1_room40_pts is the
    # season-total column already on the VORP board (not a rate stat).
    df["total_pts_2025"] = pd.to_numeric(df.get("lag1_room40_pts"), errors="coerce").fillna(0).clip(lower=1)

    sharpe_min, sharpe_max = df["sharpe_calc"].min(skipna=True), df["sharpe_calc"].max(skipna=True)
    size_max = df["total_pts_2025"].max()

    for pos in positions:
        sub = df[df["position"] == pos]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["pts_sd_2025"], y=sub["pts_mean_2025"], mode="markers+text",
            name=pos, text=sub["full_name"].str.split().str[-1], textposition="top center",
            textfont=dict(size=9, color=TXT_MUTED),
            marker=dict(
                size=sub["total_pts_2025"], sizemode="area",
                sizeref=2. * max(size_max, 1) / (40. ** 2), sizemin=5,
                symbol=POS_SYMBOLS.get(pos, "circle"),
                color=sub["sharpe_calc"], coloraxis="coloraxis",
                line=dict(width=1, color=GRID), opacity=0.9,
            ),
            customdata=sub[["full_name", "team", "adp_overall", "vorp", "total_pts_2025", "sharpe_calc"]],
            hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>ADP %{customdata[2]:.0f} | "
                          "VORP %{customdata[3]:.1f}<br>SD: %{x:.1f} | Mean pts/wk: %{y:.1f}<br>"
                          "2025 total pts: %{customdata[4]:.0f} | Sharpe: %{customdata[5]:.2f}<extra></extra>",
        ))
    fig.update_layout(coloraxis=dict(
        colorscale=[[0, ACCENT_BAD], [0.5, TXT], [1, ACCENT_GOOD]],
        cmin=sharpe_min if pd.notna(sharpe_min) else 0, cmax=sharpe_max if pd.notna(sharpe_max) else 1,
        colorbar=dict(title=dict(text="Sharpe (mean/sd)", font=dict(color=TXT_MUTED)), tickfont=dict(color=TXT_MUTED)),
    ))
    navy_fig_layout(
        fig, "2025 Weekly Scoring: Volatility vs Mean",
        "x = stdev of weekly Room40 pts (2025, lower=steadier) | y = mean weekly pts | "
        "bubble size = total 2025 season pts | color = Sharpe (mean/sd)",
        x_title="Std Dev of Weekly Points (2025)", y_title="Mean Weekly Points (2025)",
    )
    return fig


def build_kdst_volatility_figure(pos, kdst_df):
    """Standalone mean-vs-volatility chart for K or DEF - season-long 2025
    data only, no live-draft drafted-player filtering (no VORP board exists
    for these positions to know who's still available). Same visual language
    (Sharpe color, bubble = games played) as build_volatility_figure."""
    fig = go.Figure()
    if kdst_df is None or kdst_df.empty:
        script = "fetchNflKickerVolatility.R" if pos == "K" else "fetchNflDstVolatility.R"
        navy_fig_layout(fig, f"2025 Season Scoring: {pos} Volatility vs Mean",
                         f"no data - run r/{script} first")
        return fig
    df = kdst_df.copy()
    sd_safe = df["sd_pts"].replace(0, pd.NA)
    df["sharpe_calc"] = pd.to_numeric(df["mean_pts"], errors="coerce") / pd.to_numeric(sd_safe, errors="coerce")
    sharpe_min, sharpe_max = df["sharpe_calc"].min(skipna=True), df["sharpe_calc"].max(skipna=True)
    size_max = df["games_played"].max()

    fig.add_trace(go.Scatter(
        x=df["sd_pts"], y=df["mean_pts"], mode="markers+text",
        name=pos, text=df["full_name"].str.split().str[-1], textposition="top center",
        textfont=dict(size=9, color=TXT_MUTED),
        marker=dict(
            size=df["games_played"], sizemode="area",
            sizeref=2. * max(size_max, 1) / (40. ** 2), sizemin=6,
            symbol="diamond" if pos == "K" else "square",
            color=df["sharpe_calc"], coloraxis="coloraxis",
            line=dict(width=1, color=GRID), opacity=0.9,
        ),
        customdata=df[["full_name", "games_played", "mean_pts", "sd_pts", "sharpe_calc"]],
        hovertemplate="<b>%{customdata[0]}</b><br>Games: %{customdata[1]}<br>"
                      "Mean pts/wk: %{customdata[2]:.1f} | SD: %{customdata[3]:.1f}<br>"
                      "Sharpe: %{customdata[4]:.2f}<extra></extra>",
    ))
    fig.update_layout(coloraxis=dict(
        colorscale=[[0, ACCENT_BAD], [0.5, TXT], [1, ACCENT_GOOD]],
        cmin=sharpe_min if pd.notna(sharpe_min) else 0, cmax=sharpe_max if pd.notna(sharpe_max) else 1,
        colorbar=dict(title=dict(text="Sharpe (mean/sd)", font=dict(color=TXT_MUTED)), tickfont=dict(color=TXT_MUTED)),
    ))
    label = "Kicker" if pos == "K" else "DEF/Special Teams"
    navy_fig_layout(
        fig, f"2025 Season Scoring: {label} Volatility vs Mean",
        "x = stdev of weekly Room40 pts (2025, lower=steadier) | y = mean weekly pts | "
        "bubble size = games played | color = Sharpe (mean/sd) | out of VORP/live-draft "
        "scope (season-long only, not drafted-player filtered)",
        x_title="Std Dev of Weekly Points (2025)", y_title="Mean Weekly Points (2025)",
    )
    return fig


def df_to_table_records(df, cols):
    out = df[cols].copy()
    for c in out.columns:
        if out[c].dtype.kind == "f":
            out[c] = out[c].round(1)
    return out.to_dict("records")


def build_app(draft_id=None, my_slot=None, poll_seconds=8):
    vorp_df_base = load_vorp_board()
    vorp_df_base = vorp_df_base[vorp_df_base["adp_overall"] <= ADP_CAP].copy()
    vorp_df_base = load_draft_context(vorp_df_base)
    vorp_df_base["norm_name"] = vorp_df_base["full_name"].map(norm_name)
    match_player._id_bridge = load_id_bridge()

    ngs_exports = load_ngs_exports()
    breakout_df = load_breakout_candidates()
    aging_curves, aging_summary = load_aging_curves()
    age_lookup = load_player_ages()
    aging_fig_static = build_aging_curve_figure(aging_curves, aging_summary)
    kdst_volatility = load_kdst_volatility()

    # draft_id/my_slot are now OPTIONAL at process startup - the app can be
    # left running across multiple mock/real drafts, with each new draft
    # loaded live via the URL input bar (see load_draft() callback below)
    # instead of restarting the process. Still support passing them as CLI
    # args for backward-compat / auto-load-on-start convenience.
    initial_config = None
    if draft_id and my_slot:
        try:
            resolved_id = _extract_draft_id(draft_id)
            meta = fetch_sleeper_draft(resolved_id)
            settings = meta.get("settings", {})
            n_teams = settings.get("teams", 12)
            n_rounds = settings.get("rounds", 15)
            initial_config = {
                "draft_id": resolved_id, "my_slot": int(my_slot),
                "n_teams": n_teams, "n_rounds": n_rounds,
                "total_picks_expected": n_teams * n_rounds,
            }
        except Exception as e:
            print(f"NOTE: could not preload draft {draft_id}: {e} \u2014 "
                  f"paste a URL into the dashboard's input bar instead.")

    app = Dash(__name__, title="Room 40 Live Draft Dashboard")
    app.layout = html.Div(style={"backgroundColor": BG, "minHeight": "100vh", "padding": "16px",
                                  "fontFamily": "Segoe UI, Arial, sans-serif", "color": TXT}, children=[
        dcc.Interval(id="poll-interval", interval=poll_seconds * 1000, n_intervals=0),
        dcc.Store(id="draft-state"),
        dcc.Store(id="draft-config", data=initial_config),

        html.H2(id="app-title", style={"color": TXT, "marginBottom": "4px"}),

        html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "12px",
                         "flexWrap": "wrap", "alignItems": "center"}, children=[
            dcc.Input(id="draft-url-input", type="text",
                      placeholder="Paste Sleeper draft URL or ID",
                      style={"flex": "2", "minWidth": "320px", "padding": "7px 10px",
                             "backgroundColor": PANEL, "color": TXT,
                             "border": f"1px solid {GRID}", "borderRadius": "4px"}),
            dcc.Input(id="my-slot-input", type="number", placeholder="Your slot #",
                      min=1, max=20,
                      style={"width": "110px", "padding": "7px 10px", "backgroundColor": PANEL,
                             "color": TXT, "border": f"1px solid {GRID}", "borderRadius": "4px"}),
            html.Button("Load Draft", id="load-draft-btn", n_clicks=0,
                        style={"padding": "7px 18px", "backgroundColor": GOLD, "color": BG,
                               "border": "none", "borderRadius": "4px", "fontWeight": "bold",
                               "cursor": "pointer"}),
            html.Div(id="load-status", style={"color": TXT_MUTED, "fontSize": "12px", "flex": "1"}),
        ]),

        html.Div(id="status-banner", style={"fontSize": "16px", "marginBottom": "16px",
                                             "padding": "10px", "backgroundColor": PANEL,
                                             "borderRadius": "6px", "border": f"1px solid {GRID}"}),

        html.Div(style={"display": "flex", "gap": "16px", "marginBottom": "20px", "flexWrap": "wrap"}, children=[
            html.Div(style={"flex": "1", "minWidth": "380px"}, children=[
                html.H4("My Roster", style={"color": TXT}),
                dash_table.DataTable(id="roster-table", **DARK_TABLE_STYLE, page_size=15),
            ]),
            html.Div(style={"flex": "1.4", "minWidth": "500px"}, children=[
                html.H4("Best Available (marginal-VORP ranked)", style={"color": TXT}),
                dash_table.DataTable(
                    id="best-available-table", **DARK_TABLE_STYLE, page_size=15,
                    sort_action="native", sort_mode="single",
                ),
                html.P("Click a column header to sort by it \u2014 click again to flip direction.",
                       style={"color": TXT_MUTED, "fontSize": "11px", "marginTop": "4px"}),
            ]),
        ]),

        # ── Single global position toggle - drives every chart/graph/table
        # below (Best Available, Volatility, NGS, Aging, Breakout). Replaces
        # the four separate per-panel position checklists/tabs that used to
        # live here - one selection now, everything downstream follows it.
        html.Div(style={"marginBottom": "20px"}, children=[
            html.Span("Position: ", style={"color": TXT_MUTED, "fontSize": "13px", "marginRight": "8px"}),
            dcc.Tabs(id="global-position-toggle", value="QB", children=[
                dcc.Tab(label=pos, value=pos,
                        style={"backgroundColor": PANEL, "color": TXT_MUTED, "border": f"1px solid {GRID}",
                               "padding": "8px 6px"},
                        selected_style={"backgroundColor": BG, "color": TXT, "border": f"1px solid {GRID}",
                                        "borderTop": f"3px solid {POS_COLORS.get(pos, GOLD)}", "padding": "8px 6px"})
                for pos in ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]
            ], style={"display": "inline-block", "width": "640px", "verticalAlign": "middle"}),
        ]),

        html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "flex-start"}, children=[
            html.Div(style={"flex": "1", "minWidth": "460px"}, children=[
                html.H4("2025 Scoring Volatility", style={"color": TXT}),
                dcc.Graph(id="volatility-graph", style={"height": "520px"}),
            ]),
            html.Div(style={"flex": "1", "minWidth": "460px"}, children=[
                html.H4("Advanced Stats (Next Gen Stats)", style={"color": TXT}),
                dcc.Graph(id="ngs-graph", style={"height": "520px"}),
            ]),
        ]),

        html.Div(style={"display": "flex", "gap": "16px", "marginTop": "24px", "flexWrap": "wrap"}, children=[
            html.Div(style={"flex": "1", "minWidth": "460px"}, children=[
                dcc.Graph(id="aging-graph", figure=aging_fig_static, style={"height": "650px"}),
            ]),
            html.Div(style={"flex": "1", "minWidth": "380px"}, children=[
                html.H4("Breakout Candidates Still Available", style={"color": TXT}),
                html.P("\u2605 composite z-score (efficiency/usage-trend/situation/draft-capital/aging), "
                       "shrunk for small samples \u2014 see /nfl cheatsheet caveat",
                       style={"color": TXT_MUTED, "fontSize": "12px"}),
                dash_table.DataTable(id="breakout-table", **DARK_TABLE_STYLE, page_size=15),
            ]),
        ]),

        html.Div(id="last-update", style={"color": TXT_MUTED, "fontSize": "11px", "marginTop": "20px"}),
    ])

    # ── Load Draft callback: lets the user paste a new draft URL/ID + slot
    # into the input bar and swap the active draft WITHOUT restarting the
    # process. This is what makes the app reusable across every mock draft -
    # host it once, paste a new URL each time. ──────────────────────────────
    @app.callback(
        Output("draft-config", "data"),
        Output("load-status", "children"),
        Output("poll-interval", "n_intervals"),
        Input("load-draft-btn", "n_clicks"),
        State("draft-url-input", "value"),
        State("my-slot-input", "value"),
        prevent_initial_call=True,
    )
    def load_draft(n_clicks, url_value, slot_value):
        if not url_value or not slot_value:
            return no_update, html.Span("Enter both a draft URL/ID and your slot number.",
                                         style={"color": ACCENT_WARN}), no_update
        try:
            resolved_id = _extract_draft_id(url_value)
            meta = fetch_sleeper_draft(resolved_id)
        except Exception as e:
            return no_update, html.Span(f"Could not load draft: {e}", style={"color": ACCENT_BAD}), no_update

        settings = meta.get("settings", {})
        n_teams = settings.get("teams", 12)
        n_rounds = settings.get("rounds", 15)
        new_config = {
            "draft_id": resolved_id, "my_slot": int(slot_value),
            "n_teams": n_teams, "n_rounds": n_rounds,
            "total_picks_expected": n_teams * n_rounds,
        }
        # Reset n_intervals to 0 so the very next tick polls the NEW draft
        # immediately rather than waiting out whatever was left of the old
        # interval countdown.
        return new_config, html.Span(
            f"Loaded draft {resolved_id} \u2014 {n_teams}-team, slot {slot_value}. Polling...",
            style={"color": ACCENT_GOOD}), 0

    @app.callback(Output("app-title", "children"), Input("draft-config", "data"))
    def update_title(config):
        if not config:
            return "Room 40 Live Draft Dashboard \u2014 paste a draft URL above to begin"
        return (f"Room 40 Live Draft Dashboard \u2014 {config['n_teams']}-team, "
                f"slot {config['my_slot']}")

    # ── Poll callback: fetch Sleeper picks, compute drafted/roster state ────
    @app.callback(
        Output("draft-state", "data"),
        Output("last-update", "children"),
        Input("poll-interval", "n_intervals"),
        State("draft-config", "data"),
    )
    def poll(_n, config):
        if not config or not config.get("draft_id"):
            return {"no_config": True}, "Paste a draft URL above and click Load Draft to begin."

        draft_id = config["draft_id"]
        my_slot = config["my_slot"]
        n_teams = config["n_teams"]
        total_picks_expected = config["total_picks_expected"]

        try:
            picks = fetch_picks_tolerant(draft_id)
        except Exception as e:
            return {"error": str(e)}, f"[{datetime.now():%H:%M:%S}] poll failed: {e}"

        if not picks:
            return {"waiting": True}, f"[{datetime.now():%H:%M:%S}] draft not started yet (0 picks) \u2014 waiting..."

        drafted_names = set()
        my_rows = []
        for p in picks:
            meta_p = p.get("metadata", {}) or {}
            first, last = meta_p.get("first_name", ""), meta_p.get("last_name", "")
            position = meta_p.get("position", p.get("position", ""))
            row = match_player(vorp_df_base, p.get("player_id"), first, last, position)
            if row is None:
                continue
            drafted_names.add(row["full_name"])
            if p.get("draft_slot") == my_slot:
                my_rows.append(row.to_dict())

        roster_counts = {}
        for r in my_rows:
            roster_counts[r["position"]] = roster_counts.get(r["position"], 0) + 1

        n_picks_made = len(picks)
        done = n_picks_made >= total_picks_expected
        next_pick_no = n_picks_made + 1
        next_slot = None if done else predict_next_slot(next_pick_no, n_teams)
        distance = None if done else picks_until_my_turn(next_pick_no, my_slot, n_teams)

        return {
            "drafted_names": list(drafted_names),
            "my_rows": my_rows,
            "roster_counts": roster_counts,
            "n_picks_made": n_picks_made,
            "done": done,
            "next_pick_no": next_pick_no,
            "next_slot": next_slot,
            "distance": distance,
        }, f"[{datetime.now():%H:%M:%S}] last poll ok \u2014 {n_picks_made} picks made"

    @app.callback(Output("status-banner", "children"), Input("draft-state", "data"))
    def update_banner(state):
        if not state:
            return "Waiting for first poll..."
        if state.get("no_config"):
            return html.Span("No draft loaded \u2014 paste a Sleeper draft URL/ID and your slot above, then click Load Draft.",
                              style={"color": TXT_MUTED})
        if state.get("error"):
            return html.Span(f"\u26a0 poll error: {state['error']}", style={"color": ACCENT_BAD})
        if state.get("waiting"):
            return html.Span("Draft not started yet \u2014 waiting for first pick...", style={"color": ACCENT_WARN})
        if state.get("done"):
            return html.Span("Draft complete \u2014 all picks made.", style={"color": ACCENT_GOOD})

        distance = state["distance"]
        needs = roster_needs_str(state["roster_counts"])
        if distance == 0:
            turn_txt = html.Span(">>> YOUR TURN NOW <<<", style={"color": GOLD, "fontWeight": "bold", "fontSize": "20px"})
        else:
            turn_txt = html.Span(f"Your turn in {distance} pick(s)", style={"color": TXT})
        return html.Div([
            turn_txt,
            html.Div(f"Pick #{state['next_pick_no']} up next (slot {state['next_slot']}) \u2014 "
                     f"{state['n_picks_made']} picks made so far.", style={"color": TXT_MUTED, "marginTop": "4px"}),
            html.Div(f"Still need: {needs}", style={"color": TXT_MUTED, "marginTop": "2px"}),
        ])

    @app.callback(
        Output("roster-table", "data"), Output("roster-table", "columns"),
        Input("draft-state", "data"),
    )
    def update_roster_table(state):
        roster_cols = [{"name": c, "id": c} for c in ["full_name", "position", "team", "adp_overall", "vorp"]]
        if not state or state.get("error") or state.get("waiting") or state.get("no_config"):
            return [], roster_cols

        my_rows = state.get("my_rows", [])
        roster_df = pd.DataFrame(my_rows) if my_rows else pd.DataFrame(columns=["full_name", "position", "team", "adp_overall", "vorp"])
        roster_data = df_to_table_records(roster_df, [c for c in roster_df.columns if c in
                                                       ["full_name", "position", "team", "adp_overall", "vorp"]]) if not roster_df.empty else []
        return roster_data, roster_cols

    def positions_for_toggle(selected_pos):
        """Global position toggle is single-select (QB/RB/WR/TE/FLEX). FLEX
        expands to its eligible positions (RB/WR/TE); a specific position
        resolves to just itself. Falls back to all four if unset.
        Defensive: a stale browser tab still holding the old multi-select
        checklist widgets can send a list here (e.g. after a server restart,
        before the page is refreshed) - unwrap to the first value in that case."""
        if isinstance(selected_pos, (list, tuple, set)):
            selected_pos = next(iter(selected_pos), None)
        if not selected_pos:
            return {"QB", "RB", "WR", "TE"}
        if selected_pos == "FLEX":
            return set(FLEX_ELIGIBLE)
        return {selected_pos}

    @app.callback(
        Output("best-available-table", "data"), Output("best-available-table", "columns"),
        Input("draft-state", "data"), Input("global-position-toggle", "value"),
    )
    def update_best_available_table(state, selected_pos):
        best_cols = [{"name": c, "id": c, "type": "numeric" if c not in ("full_name", "position", "team", "mover") else "text"}
                     for c in ["full_name", "position", "team", "adp_overall", "vorp", "value_gap",
                               "marginal_gain", "pts_mean_2025", "pts_sd_2025", "mover"]]
        if selected_pos in ("K", "DEF"):
            # No VORP/ADP board for K/DEF (out of moneyball-pipeline scope,
            # see load_kdst_volatility docstring) - nothing to recommend here.
            return [], best_cols
        if not state or state.get("error") or state.get("waiting") or state.get("no_config"):
            return [], best_cols

        drafted_names = set(state.get("drafted_names", []))
        available = vorp_df_base[~vorp_df_base["full_name"].isin(drafted_names)].copy()

        positions = positions_for_toggle(selected_pos)

        # Pull a larger candidate pool from recommend() before filtering by
        # position, so filtering down to e.g. "WR only" doesn't starve the
        # table to just a couple rows (recommend()'s internal shortlist is
        # already capped per-position, so a small top_n here would bias
        # against whichever position the user filters to).
        recs = recommend(state.get("my_rows", []), available, 50, state.get("roster_counts", {}))
        recs = recs[recs["position"].isin(positions)].head(15)
        best_data = df_to_table_records(recs, [c for c in [col["id"] for col in best_cols] if c in recs.columns])
        return best_data, best_cols

    @app.callback(
        Output("volatility-graph", "figure"),
        Input("draft-state", "data"), Input("global-position-toggle", "value"),
    )
    def update_volatility(state, selected_pos):
        if selected_pos in ("K", "DEF"):
            return build_kdst_volatility_figure(selected_pos, kdst_volatility.get(selected_pos))
        if not state or state.get("error") or state.get("waiting") or state.get("no_config"):
            fig = go.Figure()
            navy_fig_layout(fig, "2025 Weekly Scoring: Mean vs Volatility", "waiting for draft data...")
            return fig
        drafted_names = set(state.get("drafted_names", []))
        available = vorp_df_base[~vorp_df_base["full_name"].isin(drafted_names)].copy()
        positions = positions_for_toggle(selected_pos)
        return build_volatility_figure(available, positions)

    @app.callback(
        Output("ngs-graph", "figure"),
        Input("draft-state", "data"), Input("global-position-toggle", "value"),
    )
    def update_ngs(state, selected_pos):
        # NGS metrics (x/y axes) are position-specific and don't share a
        # common scale (e.g. RB efficiency/RYOE vs WR separation/YAC) - FLEX
        # can't be flattened into one chart without conflating unrelated
        # metrics, so it shows a prompt to pick a specific position instead.
        # K/DEF have no NGS export at all (not in scope, see docstrings above).
        if selected_pos in ("K", "DEF"):
            fig = go.Figure()
            navy_fig_layout(fig, "Advanced Stats (NGS)",
                             f"not applicable for {selected_pos} \u2014 no Next Gen Stats export for this position")
            return fig
        if selected_pos == "FLEX" or not selected_pos:
            fig = go.Figure()
            navy_fig_layout(fig, "Advanced Stats (NGS)",
                             "select a specific position (QB/RB/WR/TE) \u2014 "
                             "NGS metrics differ per position and can't combine under FLEX")
            return fig
        pos = selected_pos
        if not state or state.get("error") or state.get("waiting") or state.get("no_config"):
            fig = go.Figure()
            navy_fig_layout(fig, f"{pos} Advanced Stats (NGS)", "waiting for draft data...")
            return fig
        drafted_names = set(state.get("drafted_names", []))
        available_norm = set(vorp_df_base[~vorp_df_base["full_name"].isin(drafted_names)]["norm_name"])
        return build_ngs_figure(pos, ngs_exports.get(pos), available_norm)

    @app.callback(
        Output("breakout-table", "data"), Output("breakout-table", "columns"),
        Input("draft-state", "data"), Input("global-position-toggle", "value"),
    )
    def update_breakout(state, selected_pos):
        cols = [{"name": c, "id": c} for c in ["full_name", "position", "team", "adp_overall", "vorp", "breakout_z"]]
        if not state or state.get("error") or state.get("waiting") or state.get("no_config") or breakout_df.empty:
            return [], cols
        drafted_names = set(state.get("drafted_names", []))
        available = vorp_df_base[~vorp_df_base["full_name"].isin(drafted_names)].copy()
        merged = available.merge(breakout_df[["norm_name", "position", "breakout_z"]],
                                  on=["norm_name", "position"], how="inner")
        positions = positions_for_toggle(selected_pos)
        merged = merged[merged["position"].isin(positions)]
        merged = merged.sort_values("breakout_z", ascending=False)
        return df_to_table_records(merged, [c["id"] for c in cols]), cols

    @app.callback(
        Output("aging-graph", "figure"),
        Input("draft-state", "data"), Input("global-position-toggle", "value"),
    )
    def update_aging(state, selected_pos):
        positions = list(positions_for_toggle(selected_pos))
        if not state or state.get("error") or state.get("waiting") or state.get("no_config"):
            return build_aging_curve_figure(aging_curves, aging_summary, None, age_lookup, positions=positions)
        drafted_names = set(state.get("drafted_names", []))
        available = vorp_df_base[~vorp_df_base["full_name"].isin(drafted_names)].copy()
        return build_aging_curve_figure(aging_curves, aging_summary, available, age_lookup, positions=positions)

    return app


def main():
    ap = argparse.ArgumentParser(description="Live Sleeper draft dashboard - visual UI. "
                                               "draft_id/my_slot are now OPTIONAL - if omitted, "
                                               "start the app blank and load/switch drafts live "
                                               "from the URL input bar in the browser (no restart "
                                               "needed between mock drafts).")
    ap.add_argument("draft_id", nargs="?", default=None,
                     help="optional: bare Sleeper draft ID or full URL to auto-load on start")
    ap.add_argument("my_slot", type=int, nargs="?", default=None,
                     help="optional: your draft-order slot, required if draft_id is given")
    ap.add_argument("--poll", type=int, default=8, help="seconds between polls (default 8)")
    ap.add_argument("--port", type=int, default=8877)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if args.draft_id and args.my_slot is None:
        ap.error("my_slot is required when draft_id is given")

    app = build_app(args.draft_id, args.my_slot, args.poll)
    print(f"\nOpen http://{args.host}:{args.port} in your browser.")
    if not args.draft_id:
        print("No draft preloaded - paste a Sleeper draft URL/ID and your slot into the "
              "dashboard's input bar, then click 'Load Draft'. The app stays running across "
              "mock drafts - just paste a new URL to switch.\n")
    else:
        print()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
