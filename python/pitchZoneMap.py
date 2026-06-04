#!/usr/bin/env python3
"""
pitchZoneMap.py - Pitcher pitch location heatmap by pitch type
Usage: python3 pitchZoneMap.py "Pitcher Name" [season|last30|last7]
Writes: ~/discordBot/outputs/sports/mlb/pitchzone_{id}.png
"""

import sys, os, requests, unicodedata, datetime, math
from io import StringIO
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D

BASE    = Path(os.path.expanduser("~/discordBot"))
OUT_DIR = BASE / "outputs/sports/mlb"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://baseballsavant.mlb.com/statcast_search",
}

CURRENT_YEAR = datetime.date.today().year
TODAY        = datetime.date.today()

# Pitch type colors - consistent with Baseball Savant palette
PITCH_COLORS = {
    "4-Seam Fastball":  "#D32F2F",  # red
    "2-Seam Fastball":  "#E57373",  # light red
    "Sinker":           "#FF8A65",  # orange-red
    "Cutter":           "#FF7043",  # deep orange
    "Slider":           "#1565C0",  # blue
    "Sweeper":          "#42A5F5",  # light blue
    "Slurve":           "#7986CB",  # indigo
    "Curveball":        "#00897B",  # teal
    "Knuckle Curve":    "#26A69A",  # light teal
    "Eephus":           "#66BB6A",  # green
    "Changeup":         "#8D6E63",  # brown
    "Split-Finger":     "#A1887F",  # light brown
    "Forkball":         "#BCAAA4",  # lighter brown
    "Knuckleball":      "#FDD835",  # yellow
    "Screwball":        "#CE93D8",  # purple
    "Other":            "#9E9E9E",  # grey
}

def color_for(pitch_name: str) -> str:
    return PITCH_COLORS.get(pitch_name, PITCH_COLORS["Other"])

def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("ascii").strip().lower()

def resolve_player(name: str) -> dict | None:
    try:
        r = requests.get(
            f"https://baseballsavant.mlb.com/player/search-all?search={requests.utils.quote(name)}",
            headers=HEADERS, timeout=10
        )
        results = r.json()
        if not results:
            return None
        # prefer pitchers
        pitchers = [p for p in results if p.get("pos","").upper() in ("LHP","RHP","SP","RP","P")]
        p = pitchers[0] if pitchers else results[0]
        return {"id": p["id"], "name": p.get("name", name), "team": p.get("name_display_club",""), "pos": p.get("pos","")}
    except Exception as e:
        print(f"[pitchzone] resolve failed for '{name}': {e}", file=sys.stderr)
        return None

def fetch_pitches(pitcher_id: int, window: str) -> pd.DataFrame:
    """Fetch pitch-by-pitch Statcast data."""
    if window == "last7":
        start = (TODAY - datetime.timedelta(days=7)).isoformat()
        end   = TODAY.isoformat()
        date_params = f"&game_date_gt={start}&game_date_lt={end}"
        season_param = ""
    elif window == "last30":
        start = (TODAY - datetime.timedelta(days=30)).isoformat()
        end   = TODAY.isoformat()
        date_params = f"&game_date_gt={start}&game_date_lt={end}"
        season_param = ""
    else:
        date_params  = ""
        season_param = f"&hfSea={CURRENT_YEAR}%7C"

    url = (
        "https://baseballsavant.mlb.com/statcast_search/csv"
        f"?all=true{season_param}&player_type=pitcher"
        f"&pitchers_lookup%5B%5D={pitcher_id}"
        f"&type=details&hfGT=R%7C&min_results=0&min_pas=0"
        f"&sort_col=pitches&sort_order=desc"
        f"{date_params}"
    )

    from concurrent.futures import ThreadPoolExecutor
    import time

    def _fetch():
        r = requests.get(url, headers=HEADERS, timeout=(8, 35))
        r.raise_for_status()
        raw = r.text.strip()
        if not raw or raw.startswith("<!") or raw.startswith("{"):
            return pd.DataFrame()
        return pd.read_csv(StringIO(raw), low_memory=False)

    for attempt in range(2):
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_fetch)
                return future.result(timeout=40)
        except Exception as e:
            if attempt == 0:
                print(f"  [warn] fetch retry: {e}", file=sys.stderr)
                time.sleep(1)
            else:
                print(f"  [warn] fetch gave up: {e}", file=sys.stderr)
    return pd.DataFrame()

def draw_zone(ax, sz_top: float, sz_bot: float):
    """Draw strike zone rectangle and shadow chase zone."""
    # standard zone width is 17 inches = ~0.708 ft each side from center
    zone_left  = -0.708
    zone_right =  0.708
    zone_w     = zone_right - zone_left
    zone_h     = sz_top - sz_bot

    # chase zone (outer shadow)
    chase_pad = 0.25
    chase = patches.Rectangle(
        (zone_left - chase_pad, sz_bot - chase_pad),
        zone_w + chase_pad * 2,
        zone_h + chase_pad * 2,
        linewidth=1,
        edgecolor="#555555",
        facecolor="none",
        linestyle="--",
        zorder=2,
        alpha=0.5,
    )
    ax.add_patch(chase)

    # 3x3 zone grid (9 zones)
    col_w = zone_w / 3
    row_h = zone_h / 3
    for row in range(3):
        for col in range(3):
            rect = patches.Rectangle(
                (zone_left + col * col_w, sz_bot + row * row_h),
                col_w, row_h,
                linewidth=0.5,
                edgecolor="#888888",
                facecolor="none",
                zorder=2,
            )
            ax.add_patch(rect)

    # outer zone border (thicker)
    border = patches.Rectangle(
        (zone_left, sz_bot),
        zone_w, zone_h,
        linewidth=2,
        edgecolor="white",
        facecolor="none",
        zorder=3,
    )
    ax.add_patch(border)

def make_plot(df: pd.DataFrame, pitcher_name: str, team: str, window: str, pitcher_id: int):
    """Build the subplot grid and save to PNG."""

    # filter to pitches with location data
    df = df.dropna(subset=["plate_x", "plate_z", "pitch_name"])
    df = df[df["pitch_name"].str.strip() != ""]

    if df.empty:
        print("[pitchzone] no pitch location data", file=sys.stderr)
        sys.exit(1)

    # average strike zone
    sz_top = float(df["sz_top"].median()) if "sz_top" in df.columns else 3.50
    sz_bot = float(df["sz_bot"].median()) if "sz_bot" in df.columns else 1.50

    # get pitch types sorted by frequency
    pitch_counts = df["pitch_name"].value_counts()
    pitch_types  = pitch_counts.index.tolist()
    n_types      = len(pitch_types)
    total_pitches = len(df)

    # layout: max 4 per row
    cols = min(n_types, 4)
    rows = math.ceil(n_types / cols)

    fig_w = cols * 4.0
    fig_h = rows * 4.5 + 1.2  # extra for title

    fig, axes = plt.subplots(rows, cols, figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("#0d0d14")

    # flatten axes
    if n_types == 1:
        axes = [axes]
    elif rows == 1:
        axes = list(axes)
    else:
        axes = [ax for row in axes for ax in row]

    # hide any unused subplots
    for ax in axes[n_types:]:
        ax.set_visible(False)

    # plot limits (feet, catcher's view)
    xlim = (-2.2, 2.2)
    ylim = (0.5,  5.5)

    for i, pitch_name in enumerate(pitch_types):
        ax     = axes[i]
        subset = df[df["pitch_name"] == pitch_name]
        color  = color_for(pitch_name)
        count  = len(subset)
        pct    = count / total_pitches * 100

        ax.set_facecolor("#0d0d14")
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")

        # scatter plot - alpha based on density
        alpha = max(0.25, min(0.75, 80 / count)) if count > 0 else 0.5
        ax.scatter(
            subset["plate_x"],
            subset["plate_z"],
            c=color,
            s=18,
            alpha=alpha,
            linewidths=0,
            zorder=4,
        )

        # strike zone
        draw_zone(ax, sz_top, sz_bot)

        # home plate shape (pentagon) at bottom
        plate_x = [-0.708, -0.708, 0, 0.708, 0.708]
        plate_y = [0.3, 0.5, 0.65, 0.5, 0.3]
        ax.fill(plate_x, plate_y, color="#cccccc", alpha=0.3, zorder=2)
        ax.plot(plate_x + [plate_x[0]], plate_y + [plate_y[0]], color="#aaaaaa", linewidth=0.8, zorder=3)

        # avg velo
        velo_col = "release_speed" if "release_speed" in subset.columns else None
        velo_str = ""
        if velo_col:
            avg_velo = subset[velo_col].dropna()
            if len(avg_velo) > 0:
                velo_str = f"  {avg_velo.mean():.1f} mph avg"

        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        ax.set_aspect("equal")
        ax.tick_params(colors="#555555", labelsize=7)
        ax.set_xticks([])
        ax.set_yticks([])

        # title per subplot
        ax.set_title(
            f"{pitch_name}\n{count} pitches ({pct:.0f}%){velo_str}",
            color=color,
            fontsize=9,
            fontfamily="monospace",
            pad=6,
        )

    # window label
    window_labels = {"season": f"{CURRENT_YEAR} season", "last30": "last 30 days", "last7": "last 7 days"}
    window_str = window_labels.get(window, window)

    fig.suptitle(
        f"{pitcher_name}  -  {team}  -  pitch location  -  {window_str}  ({total_pitches} pitches)",
        color="white",
        fontsize=12,
        fontfamily="monospace",
        y=0.98,
    )

    # legend note
    fig.text(
        0.5, 0.01,
        "catcher's perspective  -  dashed = chase zone  -  data: Baseball Savant",
        ha="center", color="#555555", fontsize=7, fontfamily="monospace",
    )

    plt.tight_layout(rect=[0, 0.03, 1, 0.96])

    out_path = OUT_DIR / f"pitchzone_{pitcher_id}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()

    print(f"[pitchzone] saved {out_path} ({n_types} pitch types, {total_pitches} pitches)")
    return str(out_path)

def main():
    if len(sys.argv) < 2:
        print("Usage: pitchZoneMap.py \"Pitcher Name\" [season|last30|last7]", file=sys.stderr)
        sys.exit(1)

    name   = sys.argv[1]
    window = sys.argv[2].lower() if len(sys.argv) > 2 else "season"
    if window not in ("season", "last30", "last7"):
        window = "season"

    print(f"[pitchzone] {name} / {window}")

    info = resolve_player(name)
    if not info:
        print(f"[pitchzone] player not found: {name}", file=sys.stderr)
        sys.exit(1)

    print(f"[pitchzone] found: {info['name']} ({info['team']}, ID={info['id']})")

    df = fetch_pitches(info["id"], window)
    if df.empty:
        print(f"[pitchzone] no pitch data returned", file=sys.stderr)
        sys.exit(1)

    print(f"[pitchzone] {len(df)} pitches fetched")

    out = make_plot(df, info["name"], info["team"], window, info["id"])
    print(f"ok: {out}")

if __name__ == "__main__":
    main()
