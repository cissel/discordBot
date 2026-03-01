# overJax.py
# Fetches live aircraft within 250nm of Jacksonville and renders them
# over real OpenStreetMap / CartoDB Dark Matter tiles using only
# requests + Pillow (no cartopy, pyproj, or conda required).
#
# pip install requests Pillow

import os, sys, math, requests, struct, zlib
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ── config ─────────────────────────────────────────────────────────────────────
JAX_LAT    = 30.3322
JAX_LON    = -81.6557
RADIUS_NM  = 250        # how far to fetch aircraft data
DISPLAY_NM = 150        # how far to show on the map
ZOOM       = 8
TILE_SIZE  = 256
# IMG_SIZE is computed dynamically from DISPLAY_NM in build_basemap
OUTPUT     = Path("~/discordBot/outputs/aerospace/jaxPlanes.png").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

# CartoDB Dark Matter tiles — free, no API key
TILE_URL = "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
HEADERS  = {"User-Agent": "discordBot/1.0 personal project"}

BG_COLOR = (26, 26, 46, 255)

# ── Web Mercator math (no pyproj needed) ───────────────────────────────────────
def ll_to_tile(lat, lon, zoom):
    """Return fractional tile x,y for a lat/lon at a given zoom."""
    n    = 2 ** zoom
    xtile = (lon + 180.0) / 360.0 * n
    ytile = (1.0 - math.log(math.tan(math.radians(lat)) +
             1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return xtile, ytile

def tile_to_ll(xtile, ytile, zoom):
    """Return lat/lon for a tile coordinate."""
    n   = 2 ** zoom
    lon = xtile / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ytile / n))))
    return lat, lon

def ll_to_px(lat, lon, origin_tx, origin_ty, zoom):
    """Convert lat/lon to pixel coords relative to our stitched image origin."""
    tx, ty = ll_to_tile(lat, lon, zoom)
    px = (tx - origin_tx) * TILE_SIZE
    py = (ty - origin_ty) * TILE_SIZE
    return int(px), int(py)

def nm_to_px(nm, lat, zoom):
    """Approximate: how many pixels does nm correspond to at this lat/zoom."""
    meters_per_px = 156543.03 * math.cos(math.radians(lat)) / (2 ** zoom)
    return int(nm * 1852 / meters_per_px)

# ── tile fetching ──────────────────────────────────────────────────────────────
def fetch_tile(z, x, y):
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        print(f"[overJax] tile {z}/{x}/{y} failed: {e}", file=sys.stderr)
        img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (22, 33, 62, 255))
        return img

def build_basemap(center_lat, center_lon, zoom, display_nm):
    """Fetch and stitch tiles cropped exactly to display_nm radius."""
    # How many pixels is display_nm at this zoom?
    half_px      = nm_to_px(display_nm, center_lat, zoom)
    img_size     = half_px * 2

    ctx, cty     = ll_to_tile(center_lat, center_lon, zoom)
    tiles_needed = math.ceil(img_size / TILE_SIZE) + 2
    half         = tiles_needed / 2.0

    x0 = int(ctx - half)
    y0 = int(cty - half)
    x1 = x0 + tiles_needed + 1
    y1 = y0 + tiles_needed + 1

    canvas_w = (x1 - x0) * TILE_SIZE
    canvas_h = (y1 - y0) * TILE_SIZE
    canvas   = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR[:3])

    max_tile = 2 ** zoom
    for tx in range(x0, x1):
        for ty in range(y0, y1):
            tile = fetch_tile(zoom, tx % max_tile, ty)
            px   = (tx - x0) * TILE_SIZE
            py   = (ty - y0) * TILE_SIZE
            canvas.paste(tile, (px, py))

    # crop precisely to half_px in each direction from center
    cx_px  = (ctx - x0) * TILE_SIZE
    cy_px  = (cty - y0) * TILE_SIZE
    left   = int(cx_px - half_px)
    top    = int(cy_px - half_px)
    right  = left + img_size
    bottom = top  + img_size
    cropped = canvas.crop((left, top, right, bottom))

    origin_tx = x0 + left / TILE_SIZE
    origin_ty = y0 + top  / TILE_SIZE

    return cropped, origin_tx, origin_ty, img_size

# ── aircraft color by altitude ─────────────────────────────────────────────────
def alt_color(alt):
    if alt is None or alt == "ground": return (255, 204,   0, 220)   # yellow
    try:    a = int(alt)
    except: return (170, 170, 170, 220)
    if a < 5000:  return (255,  68,  68, 220)   # red
    if a < 15000: return (255, 136,   0, 220)   # orange
    if a < 25000: return (255, 255,  68, 220)   # yellow
    if a < 35000: return ( 68, 255, 136, 220)   # green
    return               ( 68, 204, 255, 220)   # cyan

def alt_label(alt):
    if alt is None or alt == "ground": return "GND"
    try:    return f"{int(alt)//100:03d}"
    except: return "???"

# ── draw aircraft triangle pointing in track direction ─────────────────────────
def draw_aircraft(draw, px, py, track_deg, color, size=7):
    if track_deg is None:
        track_deg = 0
    rad  = math.radians(track_deg)
    tip  = (px + size * math.sin(rad),        py - size * math.cos(rad))
    left = (px + size * 0.5 * math.sin(rad - 2.4),
            py - size * 0.5 * math.cos(rad - 2.4))
    right= (px + size * 0.5 * math.sin(rad + 2.4),
            py - size * 0.5 * math.cos(rad + 2.4))
    draw.polygon([tip, left, (px, py), right], fill=color,
                 outline=(0, 0, 0, 180))

# ── range rings ───────────────────────────────────────────────────────────────
def draw_range_rings(draw, cx, cy, lat, zoom, max_nm=250):
    ring_color = (58, 74, 106, 160)
    for nm in [50, 100, 150, 200, 250]:
        if nm > max_nm:
            continue
        r = nm_to_px(nm, lat, zoom)
        bbox = [cx - r, cy - r, cx + r, cy + r]
        draw.ellipse(bbox, outline=ring_color, width=1)
        # label at top
        draw.text((cx + 3, cy - r - 12), f"{nm}nm",
                  fill=(80, 100, 140, 200), font=None)

# ── fetch aircraft ─────────────────────────────────────────────────────────────
def fetch_aircraft():
    url = f"https://opendata.adsb.fi/api/v3/lat/{JAX_LAT}/lon/{JAX_LON}/dist/{RADIUS_NM}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("ac", [])
    except Exception as e:
        print(f"[overJax] ADS-B API error: {e}", file=sys.stderr)
        return []

# ── legend ────────────────────────────────────────────────────────────────────
def draw_legend(draw, img_size):
    items = [
        ((68, 204, 255, 220), ">35k ft"),
        ((68, 255, 136, 220), "25-35k ft"),
        ((255, 255, 68, 220), "15-25k ft"),
        ((255, 136,  0, 220), "5-15k ft"),
        ((255,  68, 68, 220), "<5k ft"),
        ((255, 204,  0, 220), "On ground"),
    ]
    x, y = 12, img_size - 14 - (len(items) * 18) - 10
    # background box
    box_h = len(items) * 18 + 20
    draw.rectangle([x - 4, y - 8, x + 100, y + box_h],
                   fill=(13, 13, 30, 210), outline=(42, 42, 74, 200))
    draw.text((x + 2, y - 5), "Altitude", fill=(170, 170, 170, 220))
    y += 14
    for color, label in items:
        draw.rectangle([x, y, x + 10, y + 10], fill=color, outline=(0,0,0,150))
        draw.text((x + 14, y - 1), label, fill=(200, 200, 200, 220))
        y += 18

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    print("[overJax] fetching aircraft...")
    aircraft = fetch_aircraft()
    plotable = [a for a in aircraft if a.get("lat") and a.get("lon")]
    print(f"[overJax] got {len(plotable)} aircraft with positions")

    print("[overJax] building basemap...")
    basemap, origin_tx, origin_ty, IMG_SIZE = build_basemap(JAX_LAT, JAX_LON, ZOOM, DISPLAY_NM)

    # draw layer (separate RGBA image composited on top)
    overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2

    # range rings
    draw_range_rings(draw, cx, cy, JAX_LAT, ZOOM, DISPLAY_NM)

    # KJAX crosshair
    draw.line([(cx - 10, cy), (cx + 10, cy)], fill=(255,255,255,200), width=2)
    draw.line([(cx, cy - 10), (cx, cy + 10)], fill=(255,255,255,200), width=2)
    draw.text((cx + 5, cy - 18), "KJAX", fill=(255, 255, 255, 230))

    on_ground = 0
    airborne  = 0

    for ac in plotable:
        lat   = ac.get("lat")
        lon   = ac.get("lon")
        alt   = ac.get("alt_baro")
        track = ac.get("track")
        gs    = ac.get("gs")
        call  = (ac.get("flight") or "").strip()
        is_ground = (alt == "ground")

        px, py = ll_to_px(lat, lon, origin_tx, origin_ty, ZOOM)
        color  = alt_color(alt)

        if is_ground:
            on_ground += 1
            draw.rectangle([px-4, py-4, px+4, py+4],
                           fill=color, outline=(0,0,0,180))
        else:
            airborne += 1
            draw_aircraft(draw, px, py, track, color, size=8)
            if call:
                draw.text((px + 6, py - 8), call,
                          fill=(210, 210, 210, 200))

    # legend
    draw_legend(draw, IMG_SIZE)

    # timestamp + count banner
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    banner = (f"Live Air Traffic  |  {RADIUS_NM}nm of Jacksonville  |  "
              f"{len(plotable)} aircraft ({airborne} airborne, {on_ground} on ground)  |  {ts}")
    draw.rectangle([0, 0, IMG_SIZE, 22], fill=(13, 13, 30, 210))
    draw.text((8, 4), banner, fill=(200, 200, 200, 230))

    # composite overlay onto basemap
    result = Image.alpha_composite(basemap.convert("RGBA"), overlay)
    result.convert("RGB").save(OUTPUT, "PNG", optimize=True)
    print(f"[overJax] saved {OUTPUT}")

if __name__ == "__main__":
    main()