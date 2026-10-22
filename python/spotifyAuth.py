#!/usr/bin/env python3
"""Per-user Spotify OAuth for the discord bot's /spotify commands.

One shared Spotify app (client_id/client_secret/redirect_uri, registered on
James's Spotify Developer dashboard) is used to authorize MANY different
Discord users, each getting their own refresh_token so /spotify genres and
/spotify artists reflect whoever ran the command, not just James.

NOTE: while this Spotify app is in Development Mode, Spotify only allows
Spotify accounts that have been added as a "tester" in the app's dashboard
(Settings -> User Management, up to 25 accounts) to complete this flow.
Anyone else hitting the authorize URL will see an "app not registered" page.

Storage:
    ~/.config/spotify-history/credentials.json
        shared app credentials: client_id, client_secret, redirect_uri
        (this file ALSO still holds James's own legacy refresh_token, used
        independently by spotify_history.py for the homelab-city billboard -
        this module never writes to that field, only reads client_id/secret/
        redirect_uri out of it)
    ~/.config/spotify-history/users/<discord_user_id>.json
        per-user: refresh_token, scope, spotify_display_name, connected_at
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_CREDS_PATH = Path.home() / ".config" / "spotify-history" / "credentials.json"
USERS_DIR = Path.home() / ".config" / "spotify-history" / "users"
SCOPE = "user-top-read"

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class NotConnected(Exception):
    """Raised when a discord user has no stored Spotify token."""


def load_app_creds() -> dict:
    with open(APP_CREDS_PATH) as f:
        d = json.load(f)
    return {"client_id": d["client_id"], "client_secret": d["client_secret"], "redirect_uri": d["redirect_uri"]}


def _user_path(discord_user_id: str) -> Path:
    return USERS_DIR / f"{discord_user_id}.json"


def load_user_token(discord_user_id: str) -> dict | None:
    p = _user_path(discord_user_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def save_user_token(discord_user_id: str, data: dict) -> None:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    p = _user_path(discord_user_id)
    p.write_text(json.dumps(data, indent=2))
    p.chmod(0o600)


def delete_user_token(discord_user_id: str) -> bool:
    p = _user_path(discord_user_id)
    if p.exists():
        p.unlink()
        return True
    return False


def build_authorize_url(state: str) -> str:
    app = load_app_creds()
    params = {
        "client_id": app["client_id"],
        "response_type": "code",
        "redirect_uri": app["redirect_uri"],
        "scope": SCOPE,
        "state": state,
        "show_dialog": "true",  # force the login/consent screen (shared household / multiple accounts)
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def extract_code(pasted: str) -> str:
    """Accepts either a bare authorization code or the full (failed) redirect
    URL the browser lands on - pulls the `code` query param out of either."""
    pasted = pasted.strip()
    if "code=" in pasted:
        parsed = urllib.parse.urlparse(pasted)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            return qs["code"][0]
    return pasted


def extract_state(pasted: str) -> str | None:
    pasted = pasted.strip()
    if "state=" in pasted:
        parsed = urllib.parse.urlparse(pasted)
        qs = urllib.parse.parse_qs(parsed.query)
        if "state" in qs:
            return qs["state"][0]
    return None


def exchange_code(discord_user_id: str, code: str) -> dict:
    """Exchanges an authorization code for tokens and saves the refresh_token
    for this discord user. Returns the Spotify profile dict (/v1/me)."""
    app = load_app_creds()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": app["redirect_uri"],
    }).encode()
    auth = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())

    access_token = body["access_token"]
    profile = _fetch_profile(access_token)

    save_user_token(discord_user_id, {
        "refresh_token": body["refresh_token"],
        "scope": body.get("scope", SCOPE),
        "spotify_display_name": profile.get("display_name") or profile.get("id"),
        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return profile


def _fetch_profile(access_token: str) -> dict:
    req = urllib.request.Request("https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def get_access_token(discord_user_id: str) -> str:
    """Refreshes and returns a live access token for this discord user.
    Raises NotConnected if they haven't run /spotify connect."""
    token = load_user_token(discord_user_id)
    if token is None:
        raise NotConnected(discord_user_id)

    app = load_app_creds()
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    }).encode()
    auth = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())

    if "refresh_token" in body and body["refresh_token"] != token.get("refresh_token"):
        token["refresh_token"] = body["refresh_token"]
        save_user_token(discord_user_id, token)

    return body["access_token"]
