#!/usr/bin/env python3
"""
discordPost.py
Minimal Discord REST poster for cron scripts that run OUTSIDE the bot
process (no discord.py event loop). Reads the bot token from
~/discordBot/.env and posts text and/or a file attachment to a channel.

    from discordPost import post
    post("markets", "hello", files=["/path/chart.png"])

Channel names map to IDs below; pass a raw numeric ID string to bypass.
"""
import os
import json
import requests

ENV_PATH = os.path.expanduser("~/discordBot/.env")
API = "https://discord.com/api/v10"
TOKEN_KEY = "DISCORD_TOKEN"

CHANNELS = {
    "markets":  "1331472292814782586",
    "test-env": "1353611337888038964",
    "room-40":  "1286546740211613709",
}


def get_token(env_path=ENV_PATH):
    with open(env_path) as f:
        for line in f:
            parts = line.strip().split("=", 1)
            if len(parts) == 2 and parts[0] == TOKEN_KEY:
                return parts[1].strip()
    raise RuntimeError(f"{TOKEN_KEY} not found in {env_path}")


def resolve_channel(name_or_id):
    s = str(name_or_id).lstrip("#")
    return CHANNELS.get(s, s)


def post(channel, content=None, files=None, timeout=30):
    """Post content (<=2000 chars; longer text is split on newlines) with
    optional file attachments to a channel. Returns list of message ids."""
    cid = resolve_channel(channel)
    headers = {"Authorization": "Bot " + get_token()}
    url = f"{API}/channels/{cid}/messages"
    ids = []

    chunks = _chunk(content or "")
    # all text chunks except the last go as plain messages; files ride on the last
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        if last and files:
            payload = {"content": chunk} if chunk else {}
            multipart = []
            for j, fp in enumerate(files):
                multipart.append((f"files[{j}]", (os.path.basename(fp), open(fp, "rb"))))
            r = requests.post(url, headers=headers,
                              data={"payload_json": json.dumps(payload)},
                              files=multipart, timeout=timeout)
        else:
            r = requests.post(url, headers={**headers, "Content-Type": "application/json"},
                              json={"content": chunk}, timeout=timeout)
        r.raise_for_status()
        ids.append(r.json().get("id"))
    return ids


def _chunk(text, limit=1990):
    if not text:
        return [""]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        out.append(cur)
    # keep code fences balanced across splits
    fixed = []
    open_fence = False
    for c in out:
        if open_fence:
            c = "```\n" + c
        n = c.count("```")
        open_fence = (n % 2 == 1)
        if open_fence:
            c = c + "\n```"
        fixed.append(c)
    return fixed


if __name__ == "__main__":
    import sys
    print(post(sys.argv[1], " ".join(sys.argv[2:]) or "ping"))
