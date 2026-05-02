#!/usr/bin/env python3
"""
YouTube OAuth 2.0 flow — opens browser, catches callback, saves tokens to .env
Run once: python bin/auth/youtube.py

Setup:
  1. Go to https://console.cloud.google.com → APIs & Services → Credentials
  2. Create a project (or select existing)
  3. Enable "YouTube Data API v3"
  4. Create OAuth 2.0 Client ID → choose "Desktop app"
  5. Add http://localhost:8080/callback as authorised redirect URI
  6. Copy Client ID and Secret, enter below
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
import time
import secrets
import webbrowser
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

GOOGLE_AUTH  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
CHANNELS_API = "https://www.googleapis.com/youtube/v3/channels"
REDIRECT     = "http://localhost:8080/callback"
SCOPES       = " ".join([
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
])

CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID")     or input("YouTube Client ID: ").strip()
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET") or input("YouTube Client Secret: ").strip()

auth_code   = None
csrf_state  = secrets.token_urlsafe(16)
server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self._respond("<h2>YouTube authorised — you can close this tab.</h2>")
            server_done.set()
        elif "error" in params:
            err = params.get("error_description", params.get("error", ["unknown"]))[0]
            self._respond(f"<h2>Error: {err}</h2>")
            server_done.set()
        else:
            self._respond("<h2>Waiting for authorisation…</h2>")

    def _respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def exchange_code(code: str) -> dict:
    resp = requests.post(GOOGLE_TOKEN, data={
        "code":          code,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri":  REDIRECT,
        "grant_type":    "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_channel(access_token: str) -> "dict | None":
    resp = requests.get(CHANNELS_API,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"part": "snippet", "mine": "true"},
        timeout=10,
    )
    if resp.ok:
        items = resp.json().get("items", [])
        return items[0] if items else None
    return None


def main():
    params = urlencode({
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         csrf_state,
        "access_type":   "offline",
        "prompt":        "consent",  # always return refresh_token
    })
    auth_url = f"{GOOGLE_AUTH}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for YouTube / Google login…")
    print(f"\n  {auth_url}\n")
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Exchanging for tokens…")
    tokens = exchange_code(auth_code)
    access_token  = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        print("\nNo refresh token returned.")
        print("Revoke the app's access at https://myaccount.google.com/permissions and try again.")
        sys.exit(1)

    channel = get_channel(access_token)
    if channel:
        print(f"\nConnected to channel: {channel['snippet']['title']}")
    else:
        print("\nWarning: could not fetch channel info — tokens may still be valid")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "YOUTUBE_CLIENT_ID",     CLIENT_ID)
    set_key(str(ENV_FILE), "YOUTUBE_CLIENT_SECRET", CLIENT_SECRET)
    set_key(str(ENV_FILE), "YOUTUBE_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_FILE), "YOUTUBE_REFRESH_TOKEN", refresh_token)

    print(f"\nTokens saved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth youtube")


if __name__ == "__main__":
    main()
