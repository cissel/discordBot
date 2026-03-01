# jaxShips.py
# Fetches live ship positions around Jacksonville from myshiptracking.com API
# and renders them over a CartoDB Dark Matter basemap using Pillow.
#
# pip install requests Pillow  (no new deps beyond what jaxPlanes uses)
#
# Get a free trial key at https://myshiptracking.com → account → API
# Set env var: MYSHIPTRACKING_KEY=your_key_here

import os, sys, math, requests
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw

# ── config ─────────────────────────────────────────────────────────────────────
JAX_LAT    = 30.3322
JAX_LON    = -81.6557
DISPLAY_NM = 40
ZOOM       = 11
TILE_SIZE  = 256
OUTPUT     = Path("~/discordBot/outputs/maritime/jaxShips.png").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

API_KEY  = os.environ.get("MYSHIPTRACKING_KEY", "")
ZONE_URL = "https://api.myshiptracking.com/api/v2/vessel/zone"

# bounding box — ~40nm around JAX
MINLAT = JAX_LAT - 0.6
MAXLAT = JAX_LAT + 0.6
MINLON = JAX_LON - 0.8
MAXLON = JAX_LON + 0.8

TILE_URL = "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
HEADERS  = {"User-Agent": "discordBot/1.0 personal project"}
BG_COLOR = (26, 26, 46, 255)

# ── vessel type codes → (label, color) ────────────────────────────────────────
# myshiptracking vtype field uses same ITU/IMO codes as raw AIS
def ship_style(vtype):
    t = int(vtype) if vtype else 0
    if   t == 7 or (70 <= t <= 79): return "Cargo",     (68,  200, 255, 230)  # cyan
    elif t == 8 or (80 <= t <= 89): return "Tanker",    (255,  80,  80, 230)  # red
    elif t == 6 or (60 <= t <= 69): return "Passenger", (180, 100, 255, 230)  # purple
    elif 30 <= t <= 35:             return "Fishing",   (100, 255, 100, 230)  # green
    elif t in (21,22,31,32,52):     return "Tug/SAR",   (255, 200,  50, 230)  # yellow
    elif t in (36, 37):             return "Sail",      (200, 255, 200, 230)  # mint
    else:                           return "Other",     (160, 160, 160, 220)  # grey

# ── Web Mercator math ──────────────────────────────────────────────────────────
def ll_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(math.radians(lat)) +
         1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y

def ll_to_px(lat, lon, origin_tx, origin_ty):
    tx, ty = ll_to_tile(lat, lon, ZOOM)
    return int((tx - origin_tx) * TILE_SIZE), int((ty - origin_ty) * TILE_SIZE)

def nm_to_px(nm, lat):
    mpp = 156543.03 * math.cos(math.radians(lat)) / (2 ** ZOOM)
    return int(nm * 1852 / mpp)

# ── basemap ────────────────────────────────────────────────────────────────────
def fetch_tile(z, x, y):
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGBA")
    except:
        return Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (22, 33, 62, 255))

def build_basemap():
    half_px      = nm_to_px(DISPLAY_NM, JAX_LAT)
    img_size     = half_px * 2
    ctx, cty     = ll_to_tile(JAX_LAT, JAX_LON, ZOOM)
    tiles_needed = math.ceil(img_size / TILE_SIZE) + 2
    half         = tiles_needed / 2.0
    x0 = int(ctx - half);  y0 = int(cty - half)
    x1 = x0 + tiles_needed + 1;  y1 = y0 + tiles_needed + 1
    canvas = Image.new("RGBA", ((x1-x0)*TILE_SIZE, (y1-y0)*TILE_SIZE), BG_COLOR[:3])
    max_tile = 2 ** ZOOM
    for tx in range(x0, x1):
        for ty in range(y0, y1):
            tile = fetch_tile(ZOOM, tx % max_tile, ty)
            canvas.paste(tile, ((tx-x0)*TILE_SIZE, (ty-y0)*TILE_SIZE))
    cx_px = (ctx - x0) * TILE_SIZE
    cy_px = (cty - y0) * TILE_SIZE
    left  = int(cx_px - half_px);  top = int(cy_px - half_px)
    cropped = canvas.crop((left, top, left + img_size, top + img_size))
    origin_tx = x0 + left / TILE_SIZE
    origin_ty = y0 + top  / TILE_SIZE
    return cropped, origin_tx, origin_ty, img_size

# ── draw ship ──────────────────────────────────────────────────────────────────
def draw_ship(draw, px, py, course, nav_status, color, size=10):
    moored = nav_status in (1, 5, 6)  # anchored, moored, aground
    if moored:
        draw.polygon([
            (px,        py - size),
            (px + size, py),
            (px,        py + size),
            (px - size, py),
        ], fill=color, outline=(0, 0, 0, 180))
    else:
        h   = course if (course is not None and course != 511) else 0
        rad = math.radians(h)
        tip   = (px + size * math.sin(rad),             py - size * math.cos(rad))
        left  = (px + size * 0.55 * math.sin(rad-2.2),  py - size * 0.55 * math.cos(rad-2.2))
        right = (px + size * 0.55 * math.sin(rad+2.2),  py - size * 0.55 * math.cos(rad+2.2))
        tail  = (px + size * 0.3  * math.sin(rad+math.pi), py - size * 0.3 * math.cos(rad+math.pi))
        draw.polygon([tip, left, tail, right], fill=color, outline=(0, 0, 0, 180))

# ── legend ─────────────────────────────────────────────────────────────────────
def draw_legend(draw, img_size):
    items = [
        ((68,  200, 255, 230), "Cargo"),
        ((255,  80,  80, 230), "Tanker"),
        ((180, 100, 255, 230), "Passenger"),
        ((100, 255, 100, 230), "Fishing"),
        ((255, 200,  50, 230), "Tug / SAR"),
        ((200, 255, 200, 230), "Sail"),
        ((160, 160, 160, 220), "Other"),
    ]
    x, y = 12, img_size - 14 - len(items) * 18 - 30
    draw.rectangle([x-4, y-22, x+115, y + len(items)*18 + 6],
                   fill=(13, 13, 30, 210), outline=(42, 42, 74, 200))
    draw.text((x+2, y-19), "Vessel type", fill=(170, 170, 170, 220))
    draw.text((x+2, y-8),  "▲ underway   ◆ anchored/moored", fill=(110, 110, 130, 200))
    y += 4
    for color, label in items:
        draw.rectangle([x, y, x+10, y+10], fill=color, outline=(0,0,0,150))
        draw.text((x+14, y-1), label, fill=(200, 200, 200, 220))
        y += 18

# ── fetch ──────────────────────────────────────────────────────────────────────
def fetch_ships():
    params = {
        "minlat": MINLAT, "maxlat": MAXLAT,
        "minlon": MINLON, "maxlon": MAXLON,
        "response": "extended",
    }
    headers = {**HEADERS, "x-api-key": API_KEY}
    try:
        r = requests.get(ZONE_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            print(f"[jaxShips] API error: {data}", file=sys.stderr)
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"[jaxShips] fetch error: {e}", file=sys.stderr)
        return []

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        print("[jaxShips] ERROR: MYSHIPTRACKING_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    print("[jaxShips] fetching ships...")
    ships = fetch_ships()
    print(f"[jaxShips] got {len(ships)} vessels")

    print("[jaxShips] building basemap...")
    basemap, origin_tx, origin_ty, IMG_SIZE = build_basemap()

    overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    # JAXPORT terminal marker
    port_px, port_py = ll_to_px(30.3870, -81.5730, origin_tx, origin_ty)
    if 0 <= port_px < IMG_SIZE and 0 <= port_py < IMG_SIZE:
        draw.line([(port_px-8, port_py), (port_px+8, port_py)], fill=(255,220,0,180), width=2)
        draw.line([(port_px, port_py-8), (port_px, port_py+8)], fill=(255,220,0,180), width=2)
        draw.text((port_px+6, port_py-14), "JAXPORT", fill=(255,220,0,200))

    underway = 0
    anchored = 0

    for ship in ships:
        lat        = ship.get("lat")
        lon        = ship.get("lng")  # note: myshiptracking uses "lng" not "lon"
        if lat is None or lon is None:
            continue

        name       = (ship.get("vessel_name") or "").strip()
        vtype      = ship.get("vtype", 0)
        course     = ship.get("course")
        speed      = ship.get("speed") or 0
        nav_status = ship.get("nav_status", 15)
        dest       = (ship.get("destination") or "").strip()

        label, color = ship_style(vtype)

        if nav_status in (1, 5, 6):
            anchored += 1
        else:
            underway += 1

        px, py = ll_to_px(lat, lon, origin_tx, origin_ty)
        if not (0 <= px < IMG_SIZE and 0 <= py < IMG_SIZE):
            continue

        draw_ship(draw, px, py, course, nav_status, color, size=10)

        if name:
            label_parts = [name]
            if dest and dest not in ("", "NONE", "N/A", "NO INFO"):
                label_parts.append(f"→ {dest}")
            draw.text((px + 8, py - 7), "  ".join(label_parts),
                      fill=(220, 220, 220, 210))
            if speed and speed > 0.3:
                draw.text((px + 8, py + 5), f"{speed:.1f}kn",
                          fill=(150, 150, 150, 180))

    draw_legend(draw, IMG_SIZE)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(ships)
    banner = (f"Live Marine Traffic  |  Jacksonville / JAXPORT  |  "
              f"{total} vessels ({underway} underway, {anchored} anchored/moored)  |  {ts}")
    draw.rectangle([0, 0, IMG_SIZE, 22], fill=(13, 13, 30, 210))
    draw.text((8, 4), banner, fill=(200, 200, 200, 230))

    result = Image.alpha_composite(basemap.convert("RGBA"), overlay)
    result.convert("RGB").save(OUTPUT, "PNG", optimize=True)
    print(f"[jaxShips] saved {OUTPUT}")

if __name__ == "__main__":
    main()