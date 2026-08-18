#!/usr/bin/env python3
"""
hurricaneAlert.py - NHC Atlantic tropical weather alert checker
Called by main.py's background loop every 30 minutes.

Two alert types:
  1. Outlook disturbances (from index-at.xml, the 7-day Tropical Weather Outlook
     text feed) - alerts when a new disturbance appears with medium/high formation
     chance, or when a tracked disturbance's 7-day chance upgrades to "high".
  2. Named storms (from CurrentStorms.json) - alerts when a new Atlantic storm
     appears, or when a tracked storm's category changes (intensifies/weakens).
     Downloads the storm-specific 5-day cone/track PNG (scraped from the storm's
     graphics_atN.shtml page) so main.py can attach it to the embed.
     Also alerts once, quietly, when a tracked storm drops off the active list
     (dissipated / post-tropical / made landfall and faded).

Scope note: this is basin-wide (all Atlantic systems), not yet filtered to a
Jacksonville/Duval distance threshold - that's a deliberate follow-up, not
covered here.

State is stored in: ~/discordBot/outputs/weather/hurricane_alerts.json
{
  "outlook": {
    "<disturbance_id>": {
      "name": "...",
      "last_7d_cat": "medium",
      "last_48h_cat": "medium"
    }
  },
  "storms": {
    "<storm_id>": {
      "name": "...",
      "last_classification": "HU",
      "last_category_label": "Hurricane (Cat 2)",
      "last_adv_num": "011"
    }
  }
}

Usage: python3 hurricaneAlert.py
Prints lines prefixed with ACTION: for the bot to act on (same convention as
launchAlert.py / flightWatchCheck.py).
"""

import sys, os, json, re
import requests
from bs4 import BeautifulSoup
from pathlib import Path

BASE       = Path(os.path.expanduser("~/discordBot"))
STATE_FILE = BASE / "outputs/weather/hurricane_alerts.json"
CONE_DIR   = BASE / "outputs/weather"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

HURRICANE_CHANNEL = 1476277990470975609

CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
OUTLOOK_RSS_URL     = "https://www.nhc.noaa.gov/index-at.xml"
GRAPHICS_PAGE_TMPL  = "https://www.nhc.noaa.gov/graphics_at{n}.shtml"
OUTLOOK_IMAGE_URL   = "https://www.nhc.noaa.gov/xgtwo/two_atl_7d0.png"

# Human-facing forecast links (embedded so a click lands on the live NHC page,
# not just the static image we scraped).
OUTLOOK_FORECAST_URL = "https://www.nhc.noaa.gov/gtwo.php?basin=atlc"  # Atlantic 7-day Tropical Weather Outlook
STORM_FORECAST_TMPL  = "https://www.nhc.noaa.gov/graphics_at{n}.shtml"  # storm-specific forecast/graphics page

# NOAA logo (rendered locally from NHC's own hosted SVG via cairosvg - Wikimedia's
# thumbnail endpoint 400s on non-whitelisted sizes, so we keep a static copy instead
# of refetching it every run). Used as embed thumbnail on every hurricane alert.
NOAA_LOGO_PATH = BASE / "stickers/noaa_logo.png"

# NHC's CloudFront edge 403s on custom/bot-looking User-Agent strings (confirmed
# via curl testing) but allows browser-like ones - matches the workaround already
# used for the /billboard iTunes fetch in commands.py.
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"outlook": {}, "storms": {}}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ---------------------------------------------------------------------------
# outlook (7-day Tropical Weather Outlook) parsing
# ---------------------------------------------------------------------------
CAT_RANK = {"low": 0, "medium": 1, "high": 2}

def fetch_outlook_disturbances() -> dict:
    """Parse index-at.xml's TWO text for named disturbances (e.g. AL92) with
    their location description and 48h/7-day formation chance categories."""
    try:
        r = requests.get(OUTLOOK_RSS_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[hurricaneAlert] outlook fetch failed: {e}", file=sys.stderr)
        return {}

    soup = BeautifulSoup(r.text, "xml")
    item = soup.find("item")
    if not item or not item.description:
        return {}

    raw = item.description.text
    text = raw.replace("<br />", "\n").replace("<br/>", "\n")
    text = re.sub(r"<[^>]+>", "", text)  # strip any remaining tags

    # Split into blocks on lines like "Central Tropical Atlantic (AL92):"
    block_pattern = re.compile(
        r"^([A-Za-z .'\-]+)\s*\((A[LE]\d{2})\):\s*$", re.MULTILINE
    )
    matches = list(block_pattern.finditer(text))

    disturbances = {}
    for i, m in enumerate(matches):
        location = m.group(1).strip()
        disturbance_id = m.group(2).upper()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block_text = text[start:end].strip()

        cat48 = re.search(
            r"Formation chance through 48 hours\.{0,3}\s*(\w+)\.{0,3}\s*(\d+)\s*percent",
            block_text, re.IGNORECASE,
        )
        cat7d = re.search(
            r"Formation chance through 7 days\.{0,3}\s*(\w+)\.{0,3}\s*(\d+)\s*percent",
            block_text, re.IGNORECASE,
        )

        snippet = re.sub(r"\s*\n\s*", " ", block_text).strip()
        snippet = re.sub(r"\*\s*Formation chance.*$", "", snippet).strip()
        if len(snippet) > 350:
            snippet = snippet[:347] + "..."

        disturbances[disturbance_id] = {
            "name": location,
            "cat_48h": cat48.group(1).lower() if cat48 else "low",
            "pct_48h": cat48.group(2) if cat48 else "?",
            "cat_7d":  cat7d.group(1).lower() if cat7d else "low",
            "pct_7d":  cat7d.group(2) if cat7d else "?",
            "snippet": snippet,
        }
    return disturbances

def fetch_outlook_image() -> str:
    """Download the current basin-wide 7-day Tropical Weather Outlook PNG
    (same source hurricane.R uses for /weather hurricane) and return its
    local path, or '' on failure."""
    try:
        r = requests.get(OUTLOOK_IMAGE_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[hurricaneAlert] outlook image download failed: {e}", file=sys.stderr)
        return ""
    out_path = CONE_DIR / "hurricane_outlook_alert.png"
    out_path.write_bytes(r.content)
    return str(out_path)

# ---------------------------------------------------------------------------
# named storms
# ---------------------------------------------------------------------------
CLASSIFICATION_LABELS = {
    "DB":  "Disturbance",
    "WV":  "Tropical Wave",
    "LO":  "Low Pressure Area",
    "STD": "Subtropical Depression",
    "STS": "Subtropical Storm",
    "TD":  "Tropical Depression",
    "TS":  "Tropical Storm",
    "HU":  "Hurricane",
    "PTC": "Potential Tropical Cyclone",
    "EX":  "Extratropical Cyclone",
}

def category_label(classification: str, intensity_kt) -> str:
    base = CLASSIFICATION_LABELS.get(classification, classification)
    if classification == "HU":
        try:
            kt = float(intensity_kt)
        except (TypeError, ValueError):
            kt = 0
        if kt >= 137:
            cat = 5
        elif kt >= 113:
            cat = 4
        elif kt >= 96:
            cat = 3
        elif kt >= 83:
            cat = 2
        else:
            cat = 1
        return f"Hurricane (Cat {cat})"
    return base

def category_color_emoji(classification: str, intensity_kt) -> tuple:
    label = category_label(classification, intensity_kt)
    if classification == "HU":
        if "Cat 5" in label or "Cat 4" in label:
            return 0x8B0000, "🌀"
        if "Cat 3" in label:
            return 0xFF0000, "🌀"
        return 0xFF6600, "🌀"
    if classification in ("TS", "STS", "PTC"):
        return 0xFFCC00, "🌪️"
    if classification in ("TD", "STD"):
        return 0x3399FF, "🌧️"
    return 0x888888, "ℹ️"

def compass_dir(deg) -> str:
    try:
        deg = float(deg)
    except (TypeError, ValueError):
        return "?"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    idx = int((deg / 22.5) + 0.5) % 16
    return dirs[idx]

def fetch_active_storms() -> list:
    """Return active Atlantic-basin storms from CurrentStorms.json."""
    try:
        r = requests.get(CURRENT_STORMS_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[hurricaneAlert] CurrentStorms fetch failed: {e}", file=sys.stderr)
        return []

    storms = data.get("activeStorms", []) or []
    # Atlantic basin ids start with "al" (covers Gulf of America / Caribbean too)
    return [s for s in storms if str(s.get("id", "")).lower().startswith("al")]

def fetch_cone_image(storm: dict) -> str:
    """Scrape the storm's graphics_atN.shtml page for the current 5-day cone
    (with track line + wind radii) PNG and download it locally. Returns the
    local file path, or '' on failure.

    graphics_atN.shtml itself only links the 60x48 thumbnail version of the
    cone (src contains "5day_cone_sm") - that thumbnail's <a> wraps a link to
    a secondary "refresh" detail page which is where the actual full-size
    (~900x736) PNG lives, in an <img id="coneimage">. We follow that link to
    get real resolution instead of downloading the thumbnail directly (which
    is what caused the tiny image Discord was posting)."""
    bin_number = str(storm.get("binNumber", ""))
    m = re.match(r"AT(\d+)", bin_number, re.IGNORECASE)
    if not m:
        return ""
    n = m.group(1)
    page_url = GRAPHICS_PAGE_TMPL.format(n=n)

    try:
        r = requests.get(page_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[hurricaneAlert] graphics page fetch failed ({page_url}): {e}", file=sys.stderr)
        return ""

    soup = BeautifulSoup(r.text, "html.parser")

    # Find the thumbnail <img> for the (non-experimental) 5-day cone and follow
    # its wrapping <a> to the detail page that hosts the full-size graphic.
    detail_href = None
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if "5day_cone_sm" in src or alt == "Warnings and 5-Day Cone":
            parent_a = img.find_parent("a")
            if parent_a and parent_a.get("href"):
                detail_href = parent_a["href"]
                break

    chosen = ""
    if detail_href:
        if detail_href.startswith("//"):
            detail_url = "https:" + detail_href
        elif detail_href.startswith("/"):
            detail_url = "https://www.nhc.noaa.gov" + detail_href
        elif not detail_href.startswith("http"):
            detail_url = "https://www.nhc.noaa.gov/" + detail_href
        else:
            detail_url = detail_href

        try:
            r2 = requests.get(detail_url, headers=HEADERS, timeout=15)
            r2.raise_for_status()
            detail_soup = BeautifulSoup(r2.text, "html.parser")
            full_img = detail_soup.find("img", id="coneimage")
            if full_img and full_img.get("src"):
                chosen = full_img["src"]
            else:
                m2 = re.search(
                    r"/storm_graphics/AT\d+/refresh/AL\d+_5day_cone\+png/\d+_5day_cone\.png",
                    r2.text,
                )
                if m2:
                    chosen = m2.group(0)
        except Exception as e:
            print(f"[hurricaneAlert] cone detail page fetch failed ({detail_url}): {e}", file=sys.stderr)

    if not chosen:
        # Fallback to the old thumbnail-scrape behavior (still better than nothing,
        # though it will produce a small image) if the page layout changed on us.
        candidates = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if "storm_graphics" in src and "cone" in src.lower():
                candidates.append(src)
        if not candidates:
            return ""
        preferred = [c for c in candidates if "with_line_and_wind" in c]
        chosen = preferred[0] if preferred else candidates[0]

    if chosen.startswith("//"):
        chosen = "https:" + chosen
    elif chosen.startswith("/"):
        chosen = "https://www.nhc.noaa.gov" + chosen
    elif not chosen.startswith("http"):
        chosen = "https://www.nhc.noaa.gov/" + chosen

    try:
        img_resp = requests.get(chosen, headers=HEADERS, timeout=20)
        img_resp.raise_for_status()
    except Exception as e:
        print(f"[hurricaneAlert] cone image download failed ({chosen}): {e}", file=sys.stderr)
        return ""

    out_path = CONE_DIR / f"hurricane_cone_{bin_number}.png"
    out_path.write_bytes(img_resp.content)
    return str(out_path)

def storm_forecast_link(storm: dict) -> str:
    """Human-facing NHC forecast/graphics page for this storm (same bin number
    fetch_cone_image scrapes the cone PNG from). Falls back to the NHC Atlantic
    outlook page if the storm has no parseable bin number yet."""
    bin_number = str(storm.get("binNumber", ""))
    m = re.match(r"AT(\d+)", bin_number, re.IGNORECASE)
    if m:
        return STORM_FORECAST_TMPL.format(n=m.group(1))
    return OUTLOOK_FORECAST_URL

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    state = load_state()
    outlook_state = state.setdefault("outlook", {})
    storms_state  = state.setdefault("storms", {})

    # ── outlook disturbances ────────────────────────────────────────────────
    disturbances = fetch_outlook_disturbances()
    outlook_image_path = ""  # fetched lazily, only if we actually need to alert

    for did, info in disturbances.items():
        prior = outlook_state.get(did)
        cat7d = info["cat_7d"]

        if prior is None:
            if CAT_RANK.get(cat7d, 0) >= 1:  # medium or high
                if not outlook_image_path:
                    outlook_image_path = fetch_outlook_image()
                print(f"ACTION:NEW_DISTURBANCE:{did}")
                print(f"  channel: {HURRICANE_CHANNEL}")
                print(f"  disturbance_id: {did}")
                print(f"  name: {info['name']}")
                print(f"  cat_48h: {info['cat_48h']}")
                print(f"  pct_48h: {info['pct_48h']}")
                print(f"  cat_7d: {cat7d}")
                print(f"  pct_7d: {info['pct_7d']}")
                print(f"  snippet: {info['snippet']}")
                print(f"  outlook_image: {outlook_image_path}")
                print(f"  forecast_link: {OUTLOOK_FORECAST_URL}")
            outlook_state[did] = {
                "name": info["name"], "last_7d_cat": cat7d, "last_48h_cat": info["cat_48h"],
            }
        else:
            if CAT_RANK.get(cat7d, 0) > CAT_RANK.get(prior.get("last_7d_cat", "low"), 0):
                if not outlook_image_path:
                    outlook_image_path = fetch_outlook_image()
                print(f"ACTION:DISTURBANCE_UPGRADE:{did}")
                print(f"  channel: {HURRICANE_CHANNEL}")
                print(f"  disturbance_id: {did}")
                print(f"  name: {info['name']}")
                print(f"  cat_48h: {info['cat_48h']}")
                print(f"  pct_48h: {info['pct_48h']}")
                print(f"  cat_7d: {cat7d}")
                print(f"  pct_7d: {info['pct_7d']}")
                print(f"  snippet: {info['snippet']}")
                print(f"  outlook_image: {outlook_image_path}")
                print(f"  forecast_link: {OUTLOOK_FORECAST_URL}")
            prior["last_7d_cat"]  = cat7d
            prior["last_48h_cat"] = info["cat_48h"]

    # prune outlook entries no longer mentioned (dissipated or formed into a storm)
    for did in list(outlook_state.keys()):
        if did not in disturbances:
            del outlook_state[did]

    # ── named storms ─────────────────────────────────────────────────────────
    active_storms = fetch_active_storms()
    active_ids = set()

    for storm in active_storms:
        sid = storm.get("id")
        if not sid:
            continue
        active_ids.add(sid)

        classification = storm.get("classification", "")
        intensity      = storm.get("intensity", "")
        name           = storm.get("name", "Unnamed")
        label          = category_label(classification, intensity)
        color, emoji   = category_color_emoji(classification, intensity)
        adv_num        = (storm.get("publicAdvisory") or {}).get("advNum", "")

        prior = storms_state.get(sid)

        if prior is None:
            cone_path = fetch_cone_image(storm)
            fcst_link = storm_forecast_link(storm)
            print(f"ACTION:NEW_STORM:{sid}")
            print(f"  channel: {HURRICANE_CHANNEL}")
            print(f"  name: {name}")
            print(f"  classification: {classification}")
            print(f"  category_label: {label}")
            print(f"  intensity_kt: {intensity}")
            print(f"  pressure_mb: {storm.get('pressure', '?')}")
            print(f"  lat: {storm.get('latitude', '?')}")
            print(f"  lon: {storm.get('longitude', '?')}")
            print(f"  movement_dir: {compass_dir(storm.get('movementDir'))}")
            print(f"  movement_speed: {storm.get('movementSpeed', '?')}")
            print(f"  adv_num: {adv_num}")
            print(f"  cone_image: {cone_path}")
            print(f"  color: {color:#08x}")
            print(f"  emoji: {emoji}")
            print(f"  forecast_link: {fcst_link}")
            storms_state[sid] = {
                "name": name, "last_classification": classification,
                "last_category_label": label, "last_adv_num": adv_num,
            }
        elif label != prior.get("last_category_label"):
            cone_path = fetch_cone_image(storm)
            fcst_link = storm_forecast_link(storm)
            print(f"ACTION:CATEGORY_CHANGE:{sid}")
            print(f"  channel: {HURRICANE_CHANNEL}")
            print(f"  name: {name}")
            print(f"  classification: {classification}")
            print(f"  category_label: {label}")
            print(f"  previous_label: {prior.get('last_category_label', '?')}")
            print(f"  intensity_kt: {intensity}")
            print(f"  pressure_mb: {storm.get('pressure', '?')}")
            print(f"  lat: {storm.get('latitude', '?')}")
            print(f"  lon: {storm.get('longitude', '?')}")
            print(f"  movement_dir: {compass_dir(storm.get('movementDir'))}")
            print(f"  movement_speed: {storm.get('movementSpeed', '?')}")
            print(f"  adv_num: {adv_num}")
            print(f"  cone_image: {cone_path}")
            print(f"  color: {color:#08x}")
            print(f"  emoji: {emoji}")
            print(f"  forecast_link: {fcst_link}")
            prior["last_classification"]  = classification
            prior["last_category_label"]  = label
            prior["last_adv_num"]         = adv_num
        else:
            prior["last_adv_num"] = adv_num

    # storms that dropped off the active list since last check
    for sid in list(storms_state.keys()):
        if sid not in active_ids:
            name = storms_state[sid].get("name", "Storm")
            print(f"ACTION:STORM_DISSIPATED:{sid}")
            print(f"  channel: {HURRICANE_CHANNEL}")
            print(f"  name: {name}")
            del storms_state[sid]

    save_state(state)


if __name__ == "__main__":
    main()
