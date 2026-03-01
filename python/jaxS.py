"""
jaxcams.py  —  Fetch JAX traffic camera snapshots from FL511
Usage:
  python3 jaxcams.py <group> [count]      — fetch cameras for a group
  python3 jaxcams.py --discover           — discover all JAX camera IDs from FL511

Groups: jtb | i95 | i10 | i295 | downtown | beaches | bridges | northside | southside | random

Camera IDs come from fl511.com/map/Cctv/{id}
To find more IDs: open fl511.com, click a camera, check the URL number.
Or run: python3 jaxcams.py --discover
"""

import sys
import os
import csv
import json
import random
import urllib.request
import time
from pathlib import Path

# ── Camera database ──────────────────────────────────────────────────────────
# Format: ("Friendly name", "fl511_id", ["group_tags"])
# IDs verified live at fl511.com/map/Cctv/{id}
# Run `python3 jaxcams.py --discover` to find more IDs, or
# just grab them from the URL when clicking cameras on fl511.com
CAMERAS = [
    # ── JTB / San Pablo corridor (verified live) ──────────────────────────
    ("JTB at San Pablo Rd",             "1766", ["jtb", "beaches", "southside"]),
    ("JTB / SR-202 East",               "1727", ["jtb", "beaches", "southside"]),
    ("JTB / SR-202 West",               "1268", ["jtb", "beaches", "southside"]),
]

# ── Group aliases ─────────────────────────────────────────────────────────────
GROUP_ALIASES = {
    "jtb":       "jtb",
    "i95":       "i95",   "i-95":  "i95",   "95":  "i95",
    "i10":       "i10",   "i-10":  "i10",   "10":  "i10",
    "i295":      "i295",  "i-295": "i295",  "295": "i295",
    "downtown":  "downtown",
    "beaches":   "beaches", "beach": "beaches",
    "bridges":   "bridges", "bridge": "bridges",
    "northside": "northside", "north": "northside",
    "southside": "southside", "south": "southside",
    "random":    "random",
}

BASE_URL = "https://fl511.com/map/Cctv/"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://fl511.com/",
    "Accept":     "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

# JAX bounding box for --discover mode
JAX_LAT_MIN, JAX_LAT_MAX = 30.10, 30.55
JAX_LON_MIN, JAX_LON_MAX = -82.00, -81.30

def discover_cameras():
    """
    Pull all cameras from FL511's API, filter to JAX bounding box, print results.
    Paste the output into the CAMERAS list above with appropriate tags.
    """
    print("Fetching camera list from FL511 API...")
    endpoints = [
        "https://fl511.com/api/Cctv?lang=en",
        "https://fl511.com/api/v1/cctv?lang=en",
        "https://fl511.com/api/cameras?lang=en",
    ]
    cameras = None
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={
                **HEADERS,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            cameras = json.loads(raw)
            print(f"  Got response from {url}")
            break
        except Exception as e:
            print(f"  {url} — {e}")

    if cameras is None:
        print("\nCouldn't reach FL511 API automatically.")
        print("Manual method: open fl511.com/cctv in Chrome, F12 → Network tab,")
        print("filter by XHR, reload the page, look for a request returning a JSON array.")
        print("The camera ID is the number at the end of fl511.com/map/Cctv/{ID}")
        return

    jax = []
    for cam in (cameras if isinstance(cameras, list) else cameras.get("data", [])):
        try:
            lat = float(cam.get("latitude") or cam.get("lat") or cam.get("Latitude") or 0)
            lon = float(cam.get("longitude") or cam.get("lon") or cam.get("Longitude") or 0)
            if JAX_LAT_MIN <= lat <= JAX_LAT_MAX and JAX_LON_MIN <= lon <= JAX_LON_MAX:
                cam_id = str(cam.get("id") or cam.get("cameraId") or cam.get("Id") or "?")
                name   = str(cam.get("label") or cam.get("name") or cam.get("description") or cam_id)
                jax.append((name, cam_id, lat, lon))
        except Exception:
            continue

    print(f"\nFound {len(jax)} cameras in JAX bounding box:\n")
    for name, cam_id, lat, lon in sorted(jax, key=lambda x: x[0]):
        print(f'    ("{name}", "{cam_id}", ["i95"]),  # lat={lat:.4f} lon={lon:.4f}')
    print('\n# Add these to CAMERAS in jaxcams.py with appropriate group tags.')


def get_cameras_for_group(group: str):
    tag = GROUP_ALIASES.get(group.lower().strip(), group.lower().strip())
    if tag == "random":
        return random.sample(CAMERAS, min(4, len(CAMERAS)))
    matched = [c for c in CAMERAS if tag in c[2]]
    return matched if matched else CAMERAS


def fetch_cam(cam_id: str, dest_path: str) -> bool:
    url = BASE_URL + cam_id
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            ct   = resp.headers.get("Content-Type", "")
            data = resp.read()
        # Validate it's actually an image
        is_image = "image" in ct.lower() or data[:2] == b'\xff\xd8'  # JPEG magic bytes
        if not is_image:
            print(f"  [warn] cam {cam_id}: not an image (content-type={ct})", file=sys.stderr)
            return False
        if len(data) < 2000:
            print(f"  [warn] cam {cam_id}: too small ({len(data)}b), likely offline", file=sys.stderr)
            return False
        with open(dest_path, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"  [warn] cam {cam_id}: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--discover":
        discover_cameras()
        return

    group = sys.argv[1] if len(sys.argv) > 1 else "random"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    out_dir = Path(os.path.expanduser("~/discordBot/outputs/cams"))
    out_dir.mkdir(parents=True, exist_ok=True)

    cams = get_cameras_for_group(group)
    random.shuffle(cams)
    selected = cams[:count]

    rows = []
    for i, (name, cam_id, _tags) in enumerate(selected):
        dest = out_dir / f"cam_{i}.jpg"
        print(f"  [{i+1}/{len(selected)}] {name} (id={cam_id})")
        ok = fetch_cam(cam_id, str(dest))
        print(f"    {'OK' if ok else 'FAIL'}")
        rows.append({"slot": i, "name": name, "id": cam_id,
                     "path": str(dest) if ok else "", "ok": ok})
        if i < len(selected) - 1:
            time.sleep(0.3)

    with open(out_dir / "cams.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slot", "name", "id", "path", "ok"])
        w.writeheader()
        w.writerows(rows)

    ok_count = sum(1 for r in rows if r["ok"])
    print(f"Done: {ok_count}/{len(selected)} fetched for group '{group}'")


if __name__ == "__main__":
    main()