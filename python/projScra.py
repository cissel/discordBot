#!/usr/bin/env python3
"""
Scrape Sleeper matchup data from a dynamically rendered page using a headless browser.

Target:
  https://sleeper.com/leagues/1259616442014244864/matchup

Approach:
  - Launch Chromium in headless mode (Playwright).
  - Wait for the Next.js data script tag (__NEXT_DATA__).
  - Parse and return the JSON payload (highly reliable vs scraping DOM text).
  - Optionally save to disk as JSON.

Usage:
  python scrape_sleeper_matchup.py \
      --url https://sleeper.com/leagues/1259616442014244864/matchup \
      --out matchup.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape Sleeper matchup page via headless browser.")
    p.add_argument(
        "--url",
        default="https://sleeper.com/leagues/1259616442014244864/matchup",
        help="Matchup URL to scrape (default: %(default)s)",
    )
    p.add_argument(
        "--out",
        default="matchup.json",
        help="Path to write JSON output (default: %(default)s)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=20000,
        help="Timeout in ms for page operations (default: %(default)s)",
    )
    p.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser with UI (useful for debugging).",
    )
    return p.parse_args()


def scrape_next_data(url: str, timeout_ms: int, headless: bool = True) -> dict:
    """
    Open the page with Playwright and extract the Next.js __NEXT_DATA__ JSON.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1400, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)

            # Navigate and wait for network to settle; some sites stream data, so we still wait for the script specifically
            page.goto(url, wait_until="domcontentloaded")

            # Wait for Next.js data script to exist
            # Many Next.js apps put the payload here:
            #   <script id="__NEXT_DATA__" type="application/json">...</script>
            try:
                page.wait_for_selector("script#\\__NEXT_DATA__", state="attached", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # fallback: sometimes hydration delays; give it a nudge by waiting for network idle
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
                page.wait_for_selector("script#\\__NEXT_DATA__", state="attached", timeout=timeout_ms // 2)

            # Grab the JSON text
            next_data_text = page.locator("script#\\__NEXT_DATA__").inner_text()

            data = json.loads(next_data_text)

            # Optional: if the page lazily loads, give a tiny buffer to let any data-injection finish
            # (safe no-op if already loaded)
            time.sleep(0.2)

            return data
        finally:
            browser.close()


def write_json(data: dict, out_path: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def summarize(data: dict) -> str:
    """
    Create a short human-readable summary so you can sanity-check the scrape.
    The exact shape may change as Sleeper updates; we try to be defensive.
    """
    # Common Next.js shape: {"props": {"pageProps": ...}}
    props = data.get("props", {})
    page_props = props.get("pageProps", {})

    # Try a few likely fields—adjust as needed after first run
    league_id = (
        page_props.get("league", {}).get("league_id")
        or page_props.get("league_id")
        or "unknown"
    )

    # Sometimes matchups or schedule data are nested; we attempt to locate something informative.
    # You can tailor this based on the structure you see in your saved JSON.
    candidates = []
    for key in ("matchups", "matchup", "week_matchups", "schedule", "data"):
        val = page_props.get(key)
        if val:
            candidates.append((key, type(val).__name__))

    found = ", ".join(f"{k}({t})" for k, t in candidates) if candidates else "no obvious matchup keys"

    return f"League: {league_id} | PageProps keys: {list(page_props.keys())[:10]} | Found: {found}"


def main():
    args = get_args()
    try:
        data = scrape_next_data(
            url=args.url,
            timeout_ms=args.timeout,
            headless=not args.no_headless,
        )
    except Exception as e:
        print(f"[ERROR] Failed to scrape: {e}", file=sys.stderr)
        sys.exit(1)

    # Write raw payload
    write_json(data, args.out)

    # Print quick summary to stdout
    try:
        print(summarize(data))
        print(f"Saved JSON → {args.out}")
    except Exception as e:
        print(f"Saved JSON → {args.out} (summary failed: {e})")


if __name__ == "__main__":
    main()
