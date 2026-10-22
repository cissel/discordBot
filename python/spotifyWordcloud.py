#!/usr/bin/env python3
"""Spotify taste wordclouds - genres and artists by timeframe, per discord user.

Resurrects the old tuneR (cissel/tuneR) plotGenreWordcloud / plotArtistWordcloud
functions as a standalone script the discord bot can shell out to, using the
Spotify "Get a User's Top Items" endpoint (still live as of this writing;
requires the user-top-read scope).

Each discord user authorizes their OWN Spotify account via /spotify connect
(see spotifyAuth.py) so this reflects whoever ran the command, not just James.

Usage:
    spotifyWordcloud.py <genres|artists> <short|medium|long> <discord_user_id>

Output:
    ~/discordBot/outputs/spotify/{mode}_{timeframe}_{discord_user_id}.png

Exit codes:
    0  success
    1  Spotify API / data error
    2  bad CLI usage
    3  this discord user hasn't connected Spotify yet (NOT_CONNECTED on stderr)
"""
import json
import math
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from wordcloud import WordCloud

import spotifyAuth

OUT_DIR = Path.home() / "discordBot" / "outputs" / "spotify"

# James's navy theme (BG=#02233F, TXT=white) for the chrome (title/caption/bg).
# Word colors + shape below replicate the original tune.R output exactly:
# geom_text_wordcloud(shape = "diamond") with color = factor(word) - i.e.
# ggplot2's default discrete hue palette (scale_hue: h=15..375, c=100, l=65).
BG = "#02233F"

TIMEFRAME_LABELS = {
    "short": "Last 4 Weeks",
    "medium": "Last 6 Months",
    "long": "All-Time",
}


def fetch_top_artists(access_token, time_range, limit=50):
    """Grabs up to 50 top artists (Spotify's per-request max) for the range."""
    url = f"https://api.spotify.com/v1/me/top/artists?time_range={time_range}&limit={limit}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())



def _hcl_to_rgb(h: float, c: float, l: float) -> str:
    """CIE polarLUV (HCL) -> sRGB hex, matching R's grDevices::hcl() exactly
    (D65 white point). This is the color space ggplot2's scale_hue() uses."""
    u = c * math.cos(math.radians(h))
    v = c * math.sin(math.radians(h))
    Xn, Yn, Zn = 95.047, 100.0, 108.883
    eps, kappa = 216 / 24389, 24389 / 27
    y = ((l + 16) / 116) ** 3 * Yn if l > kappa * eps else l / kappa * Yn
    u0 = 4 * Xn / (Xn + 15 * Yn + 3 * Zn)
    v0 = 9 * Yn / (Xn + 15 * Yn + 3 * Zn)
    if l == 0:
        x = z = 0.0
    else:
        up = u / (13 * l) + u0
        vp = v / (13 * l) + v0
        x = y * 9 * up / (4 * vp)
        z = y * (12 - 3 * up - 20 * vp) / (4 * vp)
    x, y, z = x / 100, y / 100, z / 100
    r_lin = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g_lin = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b_lin = 0.0557 * x - 0.2040 * y + 1.0570 * z

    def gamma(ch):
        ch = max(0.0, min(1.0, ch))
        return ch * 12.92 if ch <= 0.0031308 else 1.055 * ch ** (1 / 2.4) - 0.055

    r, g, b = (round(max(0, min(255, gamma(ch) * 255))) for ch in (r_lin, g_lin, b_lin))
    return f"#{r:02X}{g:02X}{b:02X}"


def gg_color_hue(n: int) -> list[str]:
    """ggplot2::scale_hue() default discrete palette for n categories."""
    if n <= 0:
        return []
    hues = [(15 + i * 360 / n) % 360 for i in range(n)]
    return [_hcl_to_rgb(h, 100, 65) for h in hues]


def diamond_mask(size: int = 1400, margin: int = 30) -> "np.ndarray":
    """White canvas with a black diamond (rhombus) - the wordcloud library
    draws words only in non-white mask pixels, reproducing
    geom_text_wordcloud(shape = 'diamond')."""
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)
    mid = size / 2
    pts = [(mid, margin), (size - margin, mid), (mid, size - margin), (margin, mid)]
    draw.polygon(pts, fill=0)
    return np.array(img)


def render_wordcloud(freqs: dict, title: str, caption: str, out_path: Path):
    # Deterministic per-word color = ggplot2 factor(word) discrete hue palette,
    # assigned in alphabetical order (R's factor() default level ordering).
    words_sorted = sorted(freqs.keys())
    palette = gg_color_hue(len(words_sorted))
    word_color = dict(zip(words_sorted, palette))

    def color_func(word, font_size, position, orientation, random_state=None, **kwargs):
        return word_color.get(word, "#FFFFFF")

    wc = WordCloud(
        mask=diamond_mask(),
        background_color=BG,
        color_func=color_func,
        prefer_horizontal=0.9,
        relative_scaling=0.6,
        margin=4,
        max_words=100,
    ).generate_from_frequencies(freqs)

    fig, ax = plt.subplots(figsize=(10, 10), facecolor=BG)
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.patch.set_facecolor(BG)
    ax.set_title(title, color="white", fontsize=22, pad=14)
    fig.text(0.5, 0.02, caption, color="white", fontsize=8, ha="center")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=BG, bbox_inches="tight", dpi=150)
    plt.close(fig)


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("genres", "artists") or sys.argv[2] not in TIMEFRAME_LABELS:
        print("usage: spotifyWordcloud.py <genres|artists> <short|medium|long> <discord_user_id>", file=sys.stderr)
        return 2

    mode, timeframe, discord_user_id = sys.argv[1], sys.argv[2], sys.argv[3]
    time_range = f"{timeframe}_term"

    try:
        access_token = spotifyAuth.get_access_token(discord_user_id)
    except spotifyAuth.NotConnected:
        print("NOT_CONNECTED", file=sys.stderr)
        return 3
    except urllib.error.HTTPError as e:
        print(f"ERROR refreshing access token: {e.code} {e.read()}", file=sys.stderr)
        return 1

    try:
        payload = fetch_top_artists(access_token, time_range)
    except urllib.error.HTTPError as e:
        print(f"ERROR fetching top artists: {e.code} {e.read()}", file=sys.stderr)
        return 1

    items = payload.get("items", [])
    if not items:
        print("ERROR: no top artists returned (need more listening history for this timeframe)", file=sys.stderr)
        return 1

    label = TIMEFRAME_LABELS[timeframe]
    token = spotifyAuth.load_user_token(discord_user_id) or {}
    who = token.get("spotify_display_name", "")
    title_suffix = f" - {who}" if who else ""
    caption = f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')} - /spotify {mode} {timeframe}"

    if mode == "genres":
        counts = Counter()
        for artist in items:
            for g in artist.get("genres", []):
                counts[g] += 1
        if not counts:
            print("ERROR: none of your top artists have genre tags on Spotify right now", file=sys.stderr)
            return 1
        out_path = OUT_DIR / f"genres_{timeframe}_{discord_user_id}.png"
        render_wordcloud(dict(counts), f"Top Genres - {label}{title_suffix}", caption, out_path)
    else:
        n = len(items)
        freqs = {artist["name"]: (n - i) for i, artist in enumerate(items)}
        out_path = OUT_DIR / f"artists_{timeframe}_{discord_user_id}.png"
        render_wordcloud(freqs, f"Top Artists - {label}{title_suffix}", caption, out_path)

    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
