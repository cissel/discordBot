# trackPlane.py
# Look up a specific aircraft by tail/registration number and render its
# current position + recent flight track over CartoDB Dark Matter tiles.
#
# Data sources (both free, no API key required):
#   - adsb.fi v2 API   -> current position/state by registration
#   - OpenSky Network  -> recent track history by ICAO24 hex (anonymous, rate limited)
#
# pip install requests Pillow
# Usage: python3 trackPlane.py --tail N508KD

import argparse, sys, math, json, requests
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ── CLI args ────────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser()
_parser.add_argument("--tail", required=True, help="Tail / registration number, e.g. N508KD")
_args = _parser.parse_args()
TAIL_RAW = _args.tail.strip()
TAIL = TAIL_RAW.upper().replace(" ", "")

# ── config ────────────────────────────────────────────────────────────────
TILE_SIZE = 256
TILE_URL  = "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
HEADERS   = {"User-Agent": "discordBot/1.0 personal project"}
BG_COLOR  = (26, 26, 46, 255)

OUTPUT_DIR = Path("~/discordBot/outputs/aerospace").expanduser()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT = OUTPUT_DIR / f"track_{TAIL}.png"
STATS_JSON = OUTPUT_DIR / f"track_{TAIL}.json"

ADSB_REG_URL      = "https://opendata.adsb.fi/api/v2/registration/{tail}"
ADSB_CALLSIGN_URL = "https://opendata.adsb.fi/api/v2/callsign/{callsign}"
OPENSKY_URL = "https://opensky-network.org/api/tracks/all?icao24={hex}&time=0"

# ── font loading (mirrors overJax.py) ───────────────────────────────────────
def load_fonts():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    ttf_path = next((p for p in candidates if Path(p).exists()), None)
    if ttf_path:
        return (
            ImageFont.truetype(ttf_path, 11),
            ImageFont.truetype(ttf_path, 13),
            ImageFont.truetype(ttf_path, 15),
        )
    default = ImageFont.load_default()
    return default, default, default

FONT_SM, FONT_MD, FONT_LG = load_fonts()

# ── Web Mercator math (same as overJax.py) ─────────────────────────────────
def ll_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    xtile = (lon + 180.0) / 360.0 * n
    ytile = (1.0 - math.log(math.tan(math.radians(lat)) +
             1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return xtile, ytile

def ll_to_px(lat, lon, origin_tx, origin_ty, zoom):
    tx, ty = ll_to_tile(lat, lon, zoom)
    px = (tx - origin_tx) * TILE_SIZE
    py = (ty - origin_ty) * TILE_SIZE
    return int(px), int(py)

def nm_to_px(nm, lat, zoom):
    meters_per_px = 156543.03 * math.cos(math.radians(lat)) / (2 ** zoom)
    return int(nm * 1852 / meters_per_px)

def haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065  # earth radius in nm
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# ── tile fetching / stitching ───────────────────────────────────────────────
def fetch_tile(z, x, y):
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        print(f"[trackPlane] tile {z}/{x}/{y} failed: {e}", file=sys.stderr)
        return Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (22, 33, 62, 255))

def build_basemap(center_lat, center_lon, zoom, half_nm):
    half_px  = nm_to_px(half_nm, center_lat, zoom)
    img_size = max(int(half_px * 2), 900)  # floor so small extents still render legibly

    ctx, cty = ll_to_tile(center_lat, center_lon, zoom)
    tiles_needed = math.ceil(img_size / TILE_SIZE) + 2
    half = tiles_needed / 2.0

    x0 = int(ctx - half); y0 = int(cty - half)
    x1 = x0 + tiles_needed + 1; y1 = y0 + tiles_needed + 1

    canvas_w = (x1 - x0) * TILE_SIZE
    canvas_h = (y1 - y0) * TILE_SIZE
    canvas = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR[:3])

    max_tile = 2 ** zoom
    for tx in range(x0, x1):
        for ty in range(y0, y1):
            tile = fetch_tile(zoom, tx % max_tile, ty)
            canvas.paste(tile, ((tx - x0) * TILE_SIZE, (ty - y0) * TILE_SIZE))

    cx_px = (ctx - x0) * TILE_SIZE
    cy_px = (cty - y0) * TILE_SIZE
    left  = int(cx_px - img_size / 2)
    top   = int(cy_px - img_size / 2)
    cropped = canvas.crop((left, top, left + img_size, top + img_size))

    origin_tx = x0 + left / TILE_SIZE
    origin_ty = y0 + top / TILE_SIZE
    return cropped, origin_tx, origin_ty, img_size

def pick_zoom_for_extent(half_nm):
    # wider extents need lower (more zoomed-out) zoom levels
    if half_nm > 150: return 6
    if half_nm > 75:  return 7
    if half_nm > 35:  return 8
    if half_nm > 15:  return 9
    return 10

# ── altitude coloring (mirrors overJax.py convention) ──────────────────────
def alt_color(alt):
    if alt is None or alt == "ground": return (255, 204,   0, 230)
    try: a = float(alt)
    except (TypeError, ValueError): return (170, 170, 170, 230)
    if a < 5000:  return (255,  68,  68, 230)
    if a < 15000: return (255, 136,   0, 230)
    if a < 25000: return (255, 255,  68, 230)
    if a < 35000: return ( 68, 255, 136, 230)
    return               ( 68, 204, 255, 230)

def draw_aircraft(draw, px, py, track_deg, color, size=13):
    if track_deg is None:
        track_deg = 0
    rad = math.radians(track_deg)
    tip   = (px + size * math.sin(rad),          py - size * math.cos(rad))
    left  = (px + size * 0.5 * math.sin(rad - 2.4), py - size * 0.5 * math.cos(rad - 2.4))
    right = (px + size * 0.5 * math.sin(rad + 2.4), py - size * 0.5 * math.cos(rad + 2.4))
    draw.polygon([tip, left, (px, py), right], fill=color, outline=(0, 0, 0, 230))

# ── legend (track color = altitude, mirrors /jaxplanes convention) ─────────
def draw_legend(draw, img_size, banner_h):
    items = [
        ((68, 204, 255, 230), ">35k ft"),
        ((68, 255, 136, 230), "25-35k ft"),
        ((255, 255, 68, 230), "15-25k ft"),
        ((255, 136,  0, 230), "5-15k ft"),
        ((255,  68, 68, 230), "<5k ft"),
        ((255, 204,  0, 230), "On ground"),
    ]
    swatch = 14
    spacing = 22
    x = 12
    y = img_size - 14 - (len(items) * spacing) - 10
    box_h = len(items) * spacing + 24
    draw.rectangle([x - 4, y - 22, x + 132, y + box_h - 22],
                   fill=(13, 13, 30, 215), outline=(42, 42, 74, 200))
    draw.text((x + 2, y - 19), "Track color = altitude", fill=(180, 180, 180, 230), font=FONT_SM)
    y += 0
    for color, label in items:
        draw.rectangle([x, y, x + swatch, y + swatch], fill=color, outline=(0, 0, 0, 150))
        draw.text((x + swatch + 4, y), label, fill=(210, 210, 210, 230), font=FONT_SM)
        y += spacing

# ── data fetch ──────────────────────────────────────────────────────────────
def fetch_current_state(query):
    """Try registration (tail number, e.g. N508KD) first, then fall back to
    callsign/flight number (e.g. EJA781, DAL123) since many users - especially
    passengers on a flight - only know the callsign, not the tail."""
    # attempt 1: registration/tail lookup
    try:
        r = requests.get(ADSB_REG_URL.format(tail=query), headers=HEADERS, timeout=15)
        r.raise_for_status()
        ac = r.json().get("ac") or []
        if ac:
            return ac[0], "registration"
    except Exception as e:
        print(f"[trackPlane] adsb.fi registration lookup error: {e}", file=sys.stderr)

    # attempt 2: callsign/flight number lookup
    try:
        r = requests.get(ADSB_CALLSIGN_URL.format(callsign=query), headers=HEADERS, timeout=15)
        r.raise_for_status()
        ac = r.json().get("ac") or []
        if ac:
            return ac[0], "callsign"
    except Exception as e:
        print(f"[trackPlane] adsb.fi callsign lookup error: {e}", file=sys.stderr)

    return None, None

def fetch_track(icao24_hex):
    url = OPENSKY_URL.format(hex=icao24_hex)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"[trackPlane] OpenSky track HTTP {r.status_code}", file=sys.stderr)
            return []
        data = r.json()
        return data.get("path") or []
    except Exception as e:
        print(f"[trackPlane] OpenSky track error: {e}", file=sys.stderr)
        return []

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"[trackPlane] looking up {TAIL}...")
    state, match_kind = fetch_current_state(TAIL)
    if state is None:
        print(f"NOT_FOUND: {TAIL} is not currently broadcasting ADS-B position data (tried as both tail number and callsign)")
        return

    hex_id = state.get("hex")
    cur_lat, cur_lon = state.get("lat"), state.get("lon")
    if cur_lat is None or cur_lon is None:
        print(f"NOT_FOUND: {TAIL} matched but has no current lat/lon reported")
        return

    print(f"[trackPlane] found hex={hex_id}, fetching recent track from OpenSky...")
    path = fetch_track(hex_id) if hex_id else []
    # path entries: [time, lat, lon, baro_altitude, true_track, on_ground]
    path_pts = [p for p in path if p and p[1] is not None and p[2] is not None]

    # ── determine map extent to fit the whole track + current position ──────
    lats = [cur_lat] + [p[1] for p in path_pts]
    lons = [cur_lon] + [p[2] for p in path_pts]
    lat_c = sum(lats) / len(lats)
    lon_c = sum(lons) / len(lons)

    max_dist = 5.0  # minimum half-extent in nm so a stationary/ground plane still gets a sane map
    for la, lo in zip(lats, lons):
        d = haversine_nm(lat_c, lon_c, la, lo)
        if d > max_dist:
            max_dist = d
    half_nm = max_dist * 1.35 + 3  # padding

    zoom = pick_zoom_for_extent(half_nm)
    print(f"[trackPlane] building basemap at zoom={zoom}, half_nm={half_nm:.1f}...")
    basemap, origin_tx, origin_ty, img_size = build_basemap(lat_c, lon_c, zoom, half_nm)

    overlay = Image.new("RGBA", (img_size, img_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ── draw the track as a colored polyline (color-coded by altitude) ──────
    if len(path_pts) >= 2:
        for i in range(len(path_pts) - 1):
            p0, p1 = path_pts[i], path_pts[i + 1]
            x0, y0 = ll_to_px(p0[1], p0[2], origin_tx, origin_ty, zoom)
            x1, y1 = ll_to_px(p1[1], p1[2], origin_tx, origin_ty, zoom)
            seg_color = alt_color(p1[3])
            draw.line([(x0, y0), (x1, y1)], fill=seg_color[:3] + (200,), width=3)
        # start marker
        sx, sy = ll_to_px(path_pts[0][1], path_pts[0][2], origin_tx, origin_ty, zoom)
        draw.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=(255, 255, 255, 200), outline=(0, 0, 0, 200))
        draw.text((sx + 7, sy - 6), "start", fill=(200, 200, 200, 220), font=FONT_SM)

    # ── draw current position ────────────────────────────────────────────────
    cx, cy = ll_to_px(cur_lat, cur_lon, origin_tx, origin_ty, zoom)
    color = alt_color(state.get("alt_baro"))
    draw_aircraft(draw, cx, cy, state.get("track"), color, size=15)

    callsign = (state.get("flight") or TAIL).strip()
    registration = (state.get("r") or "").strip()
    label = callsign if callsign else TAIL
    bbox = draw.textbbox((cx + 10, cy - 10), label, font=FONT_MD)
    pad = 3
    draw.rectangle([bbox[0]-pad, bbox[1]-pad, bbox[2]+pad, bbox[3]+pad], fill=(13, 13, 30, 200))
    draw.text((cx + 10, cy - 10), label, fill=(230, 230, 230, 230), font=FONT_MD)

    # legend
    draw_legend(draw, img_size, banner_h=48)

    # ── info banner ───────────────────────────────────────────────────────────
    alt = state.get("alt_baro")
    alt_str = "on ground" if alt == "ground" else (f"{int(alt):,} ft" if alt is not None else "unknown alt")
    gs = state.get("gs")
    gs_str = f"{gs:.0f} kt" if isinstance(gs, (int, float)) else "unknown spd"
    desc = state.get("desc") or state.get("t") or "unknown type"
    ownop = state.get("ownOp")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    line1 = f"✈ {TAIL}" + (f"  ({callsign})" if callsign and callsign != TAIL else "") + f"  |  {desc}"
    if registration and registration != TAIL:
        line1 += f"  |  reg {registration}"
    line2 = f"{alt_str}  |  {gs_str}  |  {ts}"
    if ownop:
        candidate = f"{line2}  |  {ownop}"
        # only append operator name if it fits comfortably within the banner width
        w = draw.textbbox((0, 0), candidate, font=FONT_SM)[2]
        if w <= img_size - 16:
            line2 = candidate

    BANNER_H = 48
    draw.rectangle([0, 0, img_size, BANNER_H], fill=(13, 13, 30, 220))
    draw.text((8, 6), line1, fill=(230, 230, 230, 235), font=FONT_LG)
    draw.text((8, 27), line2, fill=(190, 190, 190, 225), font=FONT_SM)

    result = Image.alpha_composite(basemap.convert("RGBA"), overlay)
    result.convert("RGB").save(OUTPUT, "PNG", optimize=True)
    print(f"[trackPlane] saved {OUTPUT}  ({result.width}x{result.height})  track_points={len(path_pts)}")

    # ── write stats sidecar JSON for the Discord embed ───────────────────────
    vs = state.get("baro_rate", state.get("geom_rate"))
    if isinstance(vs, (int, float)):
        if vs > 150: vs_str = f"climbing {vs:,.0f} ft/min"
        elif vs < -150: vs_str = f"descending {abs(vs):,.0f} ft/min"
        else: vs_str = "level"
    else:
        vs_str = "unknown"

    stats = {
        "tail_query":   TAIL,
        "callsign":     callsign,
        "registration": registration or TAIL,
        "hex":          hex_id,
        "aircraft_type": desc,
        "operator":     ownop or "",
        "altitude_ft":  None if alt in (None, "ground") else int(alt),
        "on_ground":    alt == "ground",
        "ground_speed_kt": gs if isinstance(gs, (int, float)) else None,
        "vertical_rate_str": vs_str,
        "heading_deg":  state.get("track"),
        "lat":          cur_lat,
        "lon":          cur_lon,
        "squawk":       state.get("squawk"),
        "updated_utc":  ts,
        "match_kind":   match_kind,
        "image_path":   str(OUTPUT),
    }
    STATS_JSON.write_text(json.dumps(stats, indent=2))
    print(f"[trackPlane] saved stats -> {STATS_JSON}")

if __name__ == "__main__":
    main()
