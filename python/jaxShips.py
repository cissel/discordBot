# jaxShips.py
# Fetches live ship positions around Jacksonville from aisstream.io (free AIS
# websocket feed, community-fed receiver network) and renders them over a
# CartoDB Dark Matter basemap using Pillow.
#
# pip install requests Pillow websockets  (no new deps beyond what jaxPlanes uses)
#
# Sign up free at https://aisstream.io (GitHub OAuth), then generate a key on
# the API Keys page. Set env var: AISSTREAM_API_KEY=your_key_here
#
# Note: aisstream.io is BETA and fed by volunteer AIS receiver stations, not a
# paid satellite+terrestrial aggregator - coverage right around JAXPORT may be
# thinner/gappier than a commercial provider. We listen for COLLECT_SECONDS and
# render whatever came in during that window (ships only transmit AIS every
# few seconds to a few minutes depending on type/speed, so a short window can
# legitimately show 0 vessels even in a busy port).

import os, sys, math, json, asyncio, requests
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw

try:
    import websockets
except ImportError:
    print("[jaxShips] ERROR: 'websockets' package not installed (pip install websockets)", file=sys.stderr)
    sys.exit(1)

# ── config ─────────────────────────────────────────────────────────────────────
JAX_LAT    = 30.3322
JAX_LON    = -81.6557
DISPLAY_NM = 40
ZOOM       = 11
TILE_SIZE  = 256
OUTPUT     = Path("~/discordBot/outputs/maritime/jaxShips.png").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

API_KEY         = os.environ.get("AISSTREAM_API_KEY", "")
AISSTREAM_URL   = "wss://stream.aisstream.io/v0/stream"
COLLECT_SECONDS = 25  # how long to listen before rendering what we have

# bounding box - ~40nm around JAX
MINLAT = JAX_LAT - 0.6
MAXLAT = JAX_LAT + 0.6
MINLON = JAX_LON - 0.8
MAXLON = JAX_LON + 0.8

TILE_URL = "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
HEADERS  = {"User-Agent": "discordBot/1.0 personal project"}
BG_COLOR = (26, 26, 46, 255)

# ── AIS ship type codes → (label, color) ──────────────────────────────────────
# aisstream ShipStaticData.Type field uses standard ITU/IMO AIS ship type codes
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
        h   = course if (course is not None and course != 360 and course != 511) else 0
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

# ── fetch via aisstream.io websocket ──────────────────────────────────────────
async def _collect_ships():
    """
    Connects to aisstream.io, subscribes to a bounding box around JAX, and
    listens for COLLECT_SECONDS. Merges PositionReport + ShipStaticData
    messages per-MMSI so we get both live lat/lon and vessel name/type/dest.
    Returns dict[mmsi] -> ship record.
    """
    ships = {}
    subscribe_msg = {
        "APIKey": API_KEY,
        "BoundingBoxes": [[[MINLAT, MINLON], [MAXLAT, MAXLON]]],
    }
    try:
        async with websockets.connect(AISSTREAM_URL, open_timeout=15) as ws:
            await ws.send(json.dumps(subscribe_msg))
            loop = asyncio.get_event_loop()
            deadline = loop.time() + COLLECT_SECONDS
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                mtype = msg.get("MessageType")
                meta  = msg.get("MetaData", {}) or {}
                mmsi  = meta.get("MMSI")
                if mmsi is None:
                    continue
                rec = ships.setdefault(mmsi, {})
                rec["name"] = (meta.get("ShipName") or rec.get("name") or "").strip()

                body = msg.get("Message", {}) or {}
                if mtype == "PositionReport":
                    pr = body.get("PositionReport", {}) or {}
                    rec["lat"]        = pr.get("Latitude", rec.get("lat"))
                    rec["lon"]        = pr.get("Longitude", rec.get("lon"))
                    rec["course"]     = pr.get("Cog", rec.get("course"))
                    rec["speed"]      = pr.get("Sog", rec.get("speed"))
                    rec["nav_status"] = pr.get("NavigationalStatus", rec.get("nav_status", 15))
                elif mtype == "ShipStaticData":
                    sd = body.get("ShipStaticData", {}) or {}
                    rec["vtype"] = sd.get("Type", rec.get("vtype", 0))
                    dest = (sd.get("Destination") or "").strip()
                    if dest:
                        rec["dest"] = dest
    except Exception as e:
        print(f"[jaxShips] websocket error: {e}", file=sys.stderr)
    return ships

def fetch_ships():
    if not API_KEY:
        return []
    raw = asyncio.run(_collect_ships())
    out = []
    for mmsi, rec in raw.items():
        if rec.get("lat") is None or rec.get("lon") is None:
            continue
        out.append({
            "mmsi": mmsi,
            "lat": rec.get("lat"),
            "lng": rec.get("lon"),
            "vessel_name": rec.get("name", ""),
            "vtype": rec.get("vtype", 0),
            "course": rec.get("course"),
            "speed": rec.get("speed") or 0,
            "nav_status": rec.get("nav_status", 15),
            "destination": rec.get("dest", ""),
        })
    return out

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        print("[jaxShips] ERROR: AISSTREAM_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    print(f"[jaxShips] listening on aisstream.io for {COLLECT_SECONDS}s...")
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
        lon        = ship.get("lng")
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
