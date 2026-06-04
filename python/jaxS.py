"""
jaxS.py - Fetch JAX traffic camera snapshots from FL511
Usage:
  python3 jaxS.py <group> [mode]
  group: i95 | i10 | i295 | jtb | beaches | bridges | downtown | northside | southside | westside | random
  mode:  grid (default, 2x2 composite) | single (one camera, full res)
"""

import sys, os, csv, random, time, requests
from pathlib import Path
from io import BytesIO
from PIL import Image

# ── Camera database ────────────────────────────────────────────────────────────
# (name, fl511_id, [group_tags])  - 196 cameras, auto-discovered from FL511 API
CAMERAS = [
    ("Airport Road W of I-95", "1582", ["i95", "northside"]),
    ("SR-23 MM 32.6 NB", "2843", ["westside"]),
    ("SR-23 MM 35.2 NB", "2845", ["westside"]),
    ("SR-23 MM 36.6 NB", "2846", ["westside"]),
    ("SR-23 MM 40.6 NB", "2850", ["westside"]),
    ("SR-23 MM 41.0 NB", "2851", ["westside"]),
    ("SR-23 MM 41.7 NB", "2852", ["westside"]),
    ("SR-23 MM 42.0 NB", "2853", ["westside"]),
    ("SR-23 MM 43.5 NB", "2854", ["westside"]),
    ("SR-23 MM 44.4 NB", "2855", ["westside"]),
    ("SR-23 MM 45.1 NB", "2856", ["westside"]),
    ("SR-23 MM 45.8 NB", "2857", ["westside"]),
    ("SR-23 MM 46.3 NB", "2858", ["westside"]),
    ("SR-23 MM 47.1 NB", "2859", ["westside"]),
    ("SR-23 MM 47.7 NB", "2860", ["westside"]),
    ("SR-23 MM 48.1 NB", "2861", ["westside"]),
    ("SR-23 MM 49.0 NB", "2862", ["westside"]),
    ("SR-23 MM 50.2 NB", "2863", ["westside"]),
    ("SR-23 MM 51.6 NB", "2864", ["westside"]),
    ("SR-23 MM 52.9 NB", "2865", ["westside"]),
    ("SR-23 MM 53.6 NB", "2866", ["westside"]),
    ("SR-23 MM 54.0 NB", "2867", ["westside"]),
    ("SR-23 MM 54.4 NB", "2868", ["westside"]),
    ("SR-23 MM 36.6 SB", "2884", ["westside"]),
    ("SR-23 MM 40.6 SB", "2888", ["westside"]),
    ("SR-23 MM 41.7 SB", "2890", ["westside"]),
    ("SR-23 MM 42.0 SB", "2891", ["westside"]),
    ("SR-23 MM 43.5 SB", "2892", ["westside"]),
    ("SR-23 MM 44.4 SB", "2893", ["westside"]),
    ("SR-23 MM 45.1 SB", "2894", ["westside"]),
    ("SR-23 MM 45.8 SB", "2895", ["westside"]),
    ("SR-23 MM 46.3 SB", "2896", ["westside"]),
    ("SR-23 MM 47.1 SB", "2897", ["westside"]),
    ("SR-23 MM 47.7 SB", "2898", ["westside"]),
    ("SR-23 MM 48.1 SB", "2899", ["westside"]),
    ("SR-23 MM 49.0 SB", "2900", ["westside"]),
    ("SR-23 MM 50.2 SB", "2901", ["westside"]),
    ("SR-23 MM 51.6 SB", "2902", ["westside"]),
    ("SR-23 MM 52.9 SB", "2903", ["westside"]),
    ("SR-23 MM 53.6 SB", "2904", ["westside"]),
    ("SR-23 MM 54.0 SB", "2905", ["westside"]),
    ("SR-23 MM 54.4 SB", "2906", ["westside"]),
    ("I-95 @ CR-2209 St Johns Pkwy SB", "1644", ["i95", "southside"]),
    ("I-95 @ SR-9B SB", "1630", ["i95", "southside"]),
    ("I-95 @ SR-9B NB", "1631", ["i95", "southside"]),
    ("I-95 at US-1 NB", "1520", ["i95", "southside"]),
    ("I-95 at US-1 SB", "1521", ["i95", "southside"]),
    ("I-95 at SR-202 / Butler Blvd NB", "1522", ["i95", "jtb", "southside"]),
    ("I-95 at SR-202 / Butler Blvd SB", "1523", ["i95", "jtb", "southside"]),
    ("I-95 at I-295 NB", "1524", ["i95", "i295"]),
    ("I-95 at I-295 SB", "1525", ["i95", "i295"]),
    ("I-95 at Old St Augustine Rd NB", "1526", ["i95", "southside"]),
    ("I-95 at Old St Augustine Rd SB", "1527", ["i95", "southside"]),
    ("I-95 at Philips Hwy NB", "1528", ["i95", "southside"]),
    ("I-95 at Philips Hwy SB", "1529", ["i95", "southside"]),
    ("I-95 at University Blvd NB", "1530", ["i95"]),
    ("I-95 at University Blvd SB", "1531", ["i95"]),
    ("I-95 at Beach Blvd NB", "1532", ["i95", "beaches"]),
    ("I-95 at Beach Blvd SB", "1533", ["i95", "beaches"]),
    ("I-95 at Atlantic Blvd NB", "1534", ["i95", "beaches"]),
    ("I-95 at Atlantic Blvd SB", "1535", ["i95", "beaches"]),
    ("I-95 at I-10 / I-295 NB", "1536", ["i95", "i10", "i295", "downtown"]),
    ("I-95 at I-10 / I-295 SB", "1537", ["i95", "i10", "i295", "downtown"]),
    ("I-95 at US-23 / Kings Rd NB", "1538", ["i95", "downtown"]),
    ("I-95 at US-23 / Kings Rd SB", "1539", ["i95", "downtown"]),
    ("I-95 at SR-115 / Edgewood Ave NB", "1540", ["i95", "northside"]),
    ("I-95 at SR-115 / Edgewood Ave SB", "1541", ["i95", "northside"]),
    ("I-95 at SR-104 / Dunn Ave NB", "1542", ["i95", "northside"]),
    ("I-95 at SR-104 / Dunn Ave SB", "1543", ["i95", "northside"]),
    ("I-95 at Pecan Park Rd NB", "1544", ["i95", "northside"]),
    ("I-95 at Pecan Park Rd SB", "1545", ["i95", "northside"]),
    ("I-95 at Airport Rd NB", "1546", ["i95", "northside"]),
    ("I-95 at Airport Rd SB", "1547", ["i95", "northside"]),
    ("I-95 at SR-102 / Busch Dr NB", "1548", ["i95", "northside"]),
    ("I-95 at SR-102 / Busch Dr SB", "1549", ["i95", "northside"]),
    ("I-95 at SR-9A / Lem Turner NB", "1550", ["i95", "northside"]),
    ("I-95 at SR-9A / Lem Turner SB", "1551", ["i95", "northside"]),
    ("I-10 at I-295 EB", "1560", ["i10", "i295", "westside"]),
    ("I-10 at I-295 WB", "1561", ["i10", "i295", "westside"]),
    ("I-10 at Chaffee Rd EB", "1562", ["i10", "westside"]),
    ("I-10 at Chaffee Rd WB", "1563", ["i10", "westside"]),
    ("I-10 at US-301 / Baldwin EB", "1564", ["i10", "westside"]),
    ("I-10 at US-301 / Baldwin WB", "1565", ["i10", "westside"]),
    ("I-10 at SR-115 / Cassat Ave EB", "1566", ["i10"]),
    ("I-10 at SR-115 / Cassat Ave WB", "1567", ["i10"]),
    ("I-10 at I-95 EB", "1568", ["i10", "i95", "downtown"]),
    ("I-10 at I-95 WB", "1569", ["i10", "i95", "downtown"]),
    ("I-10 at SR-228 / Myrtle Ave EB", "1570", ["i10"]),
    ("I-10 at SR-228 / Myrtle Ave WB", "1571", ["i10"]),
    ("I-10 at US-17 / Roosevelt EB", "1572", ["i10"]),
    ("I-10 at US-17 / Roosevelt WB", "1573", ["i10"]),
    ("I-295 at US-17 / Roosevelt NB", "1580", ["i295"]),
    ("I-295 at US-17 / Roosevelt SB", "1581", ["i295"]),
    ("I-295 at SR-202 / Butler Blvd NB", "1583", ["i295", "jtb", "beaches"]),
    ("I-295 at SR-202 / Butler Blvd SB", "1584", ["i295", "jtb", "beaches"]),
    ("I-295 at US-1 / Philips Hwy NB", "1585", ["i295", "southside"]),
    ("I-295 at US-1 / Philips Hwy SB", "1586", ["i295", "southside"]),
    ("I-295 at I-95 NB", "1587", ["i295", "i95"]),
    ("I-295 at I-95 SB", "1588", ["i295", "i95"]),
    ("I-295 at Beach Blvd NB", "1589", ["i295", "beaches"]),
    ("I-295 at Beach Blvd SB", "1590", ["i295", "beaches"]),
    ("I-295 at University Blvd NB", "1591", ["i295"]),
    ("I-295 at University Blvd SB", "1592", ["i295"]),
    ("I-295 at Atlantic Blvd NB", "1593", ["i295", "beaches"]),
    ("I-295 at Atlantic Blvd SB", "1594", ["i295", "beaches"]),
    ("I-295 at SR-202 East NB", "1595", ["i295", "beaches"]),
    ("I-295 at SR-202 East SB", "1596", ["i295", "beaches"]),
    ("I-295 at Monument Rd NB", "1597", ["i295"]),
    ("I-295 at Monument Rd SB", "1598", ["i295"]),
    ("I-295 at Merrill Rd NB", "1599", ["i295"]),
    ("I-295 at Merrill Rd SB", "1600", ["i295"]),
    ("I-295 at Ft Caroline Rd NB", "1601", ["i295"]),
    ("I-295 at Ft Caroline Rd SB", "1602", ["i295"]),
    ("I-295 at SR-9A / Lem Turner NB", "1603", ["i295", "northside"]),
    ("I-295 at SR-9A / Lem Turner SB", "1604", ["i295", "northside"]),
    ("I-295 at Dunn Ave NB", "1605", ["i295", "northside"]),
    ("I-295 at Dunn Ave SB", "1606", ["i295", "northside"]),
    ("I-295 at I-95 North NB", "1607", ["i295", "i95", "northside"]),
    ("I-295 at I-95 North SB", "1608", ["i295", "i95", "northside"]),
    ("I-295 at I-10 NB", "1609", ["i295", "i10", "westside"]),
    ("I-295 at I-10 SB", "1610", ["i295", "i10", "westside"]),
    ("I-295 at Blanding Blvd NB", "1611", ["i295", "westside"]),
    ("I-295 at Blanding Blvd SB", "1612", ["i295", "westside"]),
    ("I-295 at US-17 Cecil Commerce NB", "1613", ["i295", "westside"]),
    ("I-295 at US-17 Cecil Commerce SB", "1614", ["i295", "westside"]),
    ("SR-9B S of West Peyton Parkway", "1648", ["southside", "beaches"]),
    ("SR-9B N of West Peyton Pkwy", "1650", ["southside", "beaches"]),
    ("SR-9B @ Race Track Rd", "1646", ["southside", "beaches"]),
    ("SR-9B @ CR-2209 / St Johns Pkwy", "1652", ["southside", "beaches"]),
    ("JTB at San Pablo Rd", "1766", ["jtb", "beaches", "southside"]),
    ("JTB / SR-202 East of Intracoastal", "1727", ["jtb", "beaches", "southside"]),
    ("JTB / SR-202 West of Intracoastal", "1268", ["jtb", "beaches", "southside"]),
    ("SR-202 / Butler Blvd at I-295", "1830", ["jtb", "beaches", "southside", "i295"]),
    ("SR-202 / Butler Blvd East of I-295E", "1831", ["jtb", "beaches", "southside"]),
    ("Dames Point Bridge EB", "1700", ["bridges", "northside"]),
    ("Dames Point Bridge WB", "1701", ["bridges", "northside"]),
    ("Napoleon Bonaparte Broward Bridge EB", "1702", ["bridges"]),
    ("Napoleon Bonaparte Broward Bridge WB", "1703", ["bridges"]),
    ("Mathews Bridge EB", "1704", ["bridges", "downtown"]),
    ("Mathews Bridge WB", "1705", ["bridges", "downtown"]),
    ("Fuller Warren Bridge NB", "1706", ["bridges", "i95"]),
    ("Fuller Warren Bridge SB", "1707", ["bridges", "i95"]),
    ("Beach Blvd at Hodges Blvd", "1750", ["beaches", "southside"]),
    ("Beach Blvd at San Pablo Rd", "1751", ["beaches", "southside"]),
    ("Atlantic Blvd at Kernan Blvd", "1752", ["beaches", "southside"]),
    ("Atlantic Blvd at Hodges Blvd", "1753", ["beaches", "southside"]),
    ("Atlantic Blvd at San Pablo Rd", "1754", ["beaches", "southside"]),
]

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_URL = "https://fl511.com/map/Cctv/"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://fl511.com/",
    "Accept":     "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
OUT_DIR  = Path(os.path.expanduser("~/discordBot/outputs/cams"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

CELL_W, CELL_H = 960, 540   # each cell in the 2x2 grid

# ── Build 2x2 grid composite ──────────────────────────────────────────────────
def build_grid(cells: list[tuple[Image.Image, str]]) -> Image.Image:
    n = len(cells)
    if n == 1:
        return cells[0][0].resize((CELL_W, CELL_H), Image.LANCZOS)
    if n == 2:
        canvas = Image.new("RGB", (CELL_W * 2, CELL_H), (0, 0, 0))
        for i, (img, _) in enumerate(cells):
            canvas.paste(img.resize((CELL_W, CELL_H), Image.LANCZOS), (i * CELL_W, 0))
        return canvas
    if n == 3:
        canvas = Image.new("RGB", (CELL_W * 2, CELL_H * 2), (0, 0, 0))
        for i, (img, _) in enumerate(cells[:2]):
            canvas.paste(img.resize((CELL_W, CELL_H), Image.LANCZOS), (i * CELL_W, 0))
        canvas.paste(cells[2][0].resize((CELL_W, CELL_H), Image.LANCZOS), (CELL_W // 2, CELL_H))
        return canvas
    # 4-up
    canvas = Image.new("RGB", (CELL_W * 2, CELL_H * 2), (0, 0, 0))
    for i, (img, _) in enumerate(cells[:4]):
        col, row = i % 2, i // 2
        canvas.paste(img.resize((CELL_W, CELL_H), Image.LANCZOS), (col * CELL_W, row * CELL_H))
    return canvas

# ── Camera selection ───────────────────────────────────────────────────────────
def select_cameras(group: str, count: int = 4, search: str = ""):
    """
    If search is provided, fuzzy-match camera names and return up to 4 hits.
    Otherwise pick randomly from the group.
    Returns list of (name, cam_id, tags) tuples.
    """
    if search:
        query = search.lower().strip()
        # score each camera: exact substring > all words present > any word present
        scored = []
        words  = query.split()
        for cam in CAMERAS:
            name_l = cam[0].lower()
            if query in name_l:
                scored.append((0, cam))
            elif all(w in name_l for w in words):
                scored.append((1, cam))
            elif any(w in name_l for w in words):
                scored.append((2, cam))
        scored.sort(key=lambda x: x[0])
        return [c for _, c in scored[:4]]

    tag = group.lower().strip()
    if tag == "random":
        pool = CAMERAS
    else:
        pool = [c for c in CAMERAS if tag in c[2]]
        if not pool:
            pool = CAMERAS
    return random.sample(pool, min(count, len(pool)))

# ── Fetch one camera image ─────────────────────────────────────────────────────
def fetch_image(cam_id: str) -> bytes | None:
    url = BASE_URL + cam_id
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            data = r.content
            if len(data) >= 2000:
                return data
            if attempt == 0:
                print(f"  [cache miss] cam {cam_id}, retrying in 2s...", file=sys.stderr)
                time.sleep(2)
        except Exception as e:
            print(f"  [error] cam {cam_id}: {e}", file=sys.stderr)
            if attempt == 0:
                time.sleep(1)
    return None

# ── Draw label on cell ────────────────────────────────────────────────────────
def draw_label(img: Image.Image, text: str, font) -> Image.Image:
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 4
    x, y = 8, img.height - th - pad * 2 - 8
    draw.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad],
                   fill=(13, 13, 30, 200))
    draw.text((x, y), text, fill=(220, 220, 220, 255), font=font)
    return img

# ── Build 2x2 grid composite ──────────────────────────────────────────────────
def build_grid(cells: list[tuple[Image.Image, str]]) -> Image.Image:
    n = len(cells)
    font = load_font(15)
    if n == 1:
        img = cells[0][0].resize((CELL_W, CELL_H), Image.LANCZOS)
        return draw_label(img, cells[0][1], font)
    if n == 2:
        canvas = Image.new("RGB", (CELL_W * 2, CELL_H), (0, 0, 0))
        for i, (img, name) in enumerate(cells):
            cell = img.resize((CELL_W, CELL_H), Image.LANCZOS)
            draw_label(cell, name, font)
            canvas.paste(cell, (i * CELL_W, 0))
        return canvas
    if n == 3:
        canvas = Image.new("RGB", (CELL_W * 2, CELL_H * 2), (0, 0, 0))
        for i, (img, name) in enumerate(cells[:2]):
            cell = img.resize((CELL_W, CELL_H), Image.LANCZOS)
            draw_label(cell, name, font)
            canvas.paste(cell, (i * CELL_W, 0))
        # third centered on bottom row
        cell = cells[2][0].resize((CELL_W, CELL_H), Image.LANCZOS)
        draw_label(cell, cells[2][1], font)
        canvas.paste(cell, (CELL_W // 2, CELL_H))
        return canvas
    # 4-up
    canvas = Image.new("RGB", (CELL_W * 2, CELL_H * 2), (0, 0, 0))
    for i, (img, name) in enumerate(cells[:4]):
        cell = img.resize((CELL_W, CELL_H), Image.LANCZOS)
        draw_label(cell, name, font)
        col, row = i % 2, i // 2
        canvas.paste(cell, (col * CELL_W, row * CELL_H))
    return canvas

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    group  = sys.argv[1] if len(sys.argv) > 1 else "random"
    mode   = sys.argv[2].lower() if len(sys.argv) > 2 else "grid"
    search = sys.argv[3] if len(sys.argv) > 3 else ""

    # search mode: find all matches, report if too many
    if search:
        query  = search.lower().strip()
        words  = query.split()
        all_matches = []
        for cam in CAMERAS:
            name_l = cam[0].lower()
            if query in name_l or all(w in name_l for w in words) or any(w in name_l for w in words):
                all_matches.append(cam)
        if len(all_matches) > 4:
            # print names for the Discord command to display as a list
            print(f"TOO_MANY:{len(all_matches)}")
            for cam in all_matches:
                print(f"  {cam[0]}")
            sys.exit(2)
        if len(all_matches) == 0:
            print("NO_MATCH")
            sys.exit(2)
        cameras = all_matches[:4]
        # force single mode if exactly 1 match
        if len(cameras) == 1:
            mode = "single"
    else:
        count   = 1 if mode == "single" else 4
        cameras = select_cameras(group, count, search="")
    rows    = []
    cells   = []

    for i, (name, cam_id, _tags) in enumerate(cameras):
        print(f"  [{i+1}/{len(cameras)}] {name} (id={cam_id})")
        data = fetch_image(cam_id)
        ok   = data is not None
        dest = OUT_DIR / f"cam_{i}.jpg"
        if ok:
            with open(dest, "wb") as f:
                f.write(data)
            if mode != "single":
                img = Image.open(BytesIO(data)).convert("RGB")
                cells.append((img, name))
        rows.append({"slot": i, "name": name, "id": cam_id,
                     "path": str(dest) if ok else "", "ok": ok, "mode": mode})

    if mode == "single":
        out_path = str(OUT_DIR / "cam_0.jpg")
        ok_count = sum(1 for r in rows if r["ok"])
        for r in rows:
            r["path"] = out_path
    else:
        if not cells:
            print("error: no cameras returned images", file=sys.stderr)
            _write_csv(rows, OUT_DIR)
            sys.exit(1)
        composite = build_grid(cells)
        out_path  = str(OUT_DIR / "cam_grid.jpg")
        composite.save(out_path, "JPEG", quality=88, optimize=True)
        ok_count  = len(cells)
        for r in rows:
            r["path"] = out_path

    _write_csv(rows, OUT_DIR)

    if mode == "single":
        print(f"ok: single" if ok_count else "error: camera offline")
    else:
        print(f"ok: grid {ok_count}/{len(cameras)}")

    if not ok_count:
        sys.exit(1)


def _write_csv(rows, out_dir):
    with open(out_dir / "cams.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slot","name","id","path","ok","mode"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
