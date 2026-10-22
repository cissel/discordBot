#!/usr/bin/env python3
"""weatherDigestPost.py
Posts the daily weather/surf digest (today's weather, tomorrow's weather,
surf forecast) to #test-env. Run by the weather-daily-forecast-digest cron
job right after weathe.R / weatherTm.R / surf4castPlot.R regenerate the
images -- the images are useful on their own (serve the /weather and /surf
slash commands), this just saves James from running those by hand to see
them.

Usage: venv/bin/python3 python/weatherDigestPost.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from discordPost import post  # noqa: E402

BASE = os.path.expanduser("~/discordBot/outputs/weather")
IMAGES = [
    ("weatherTd.png", "today"),
    ("weatherTm.png", "tomorrow"),
    ("surf_fcst.png", "surf forecast"),
]


def main():
    files, missing = [], []
    for fname, label in IMAGES:
        p = os.path.join(BASE, fname)
        if os.path.exists(p):
            files.append(p)
        else:
            missing.append(label)
    if not files:
        print("[weatherDigest] no images found, nothing to post")
        return 1
    caption = "\u2600\ufe0f daily weather/surf digest"
    if missing:
        caption += f" (missing: {', '.join(missing)})"
    ids = post("test-env", caption, files=files)
    print(f"[weatherDigest] posted {len(files)} image(s) to #test-env, message ids {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
