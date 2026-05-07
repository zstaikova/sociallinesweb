#!/usr/bin/env python3
"""
Instagram direct OAuth — bypasses Facebook Page linking.
Uses the new Instagram Login API (replaces deprecated Basic Display API, Dec 2024).

Auth URL: https://www.instagram.com/oauth/authorize
Scopes:   instagram_business_basic, instagram_business_content_publish

Requirements:
  - Same Facebook App (App ID + Secret) used for Facebook auth
  - In Facebook Developer Console → your app → Add Product → Instagram
  - Under Instagram → Instagram Login → Settings:
      • Add http://localhost:8080/callback as a Valid OAuth Redirect URI
  - Instagram account must be Business or Creator type

Run once: python bin/auth/instagram_direct.py
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

# New Instagram Login API (post-Dec 2024)
IG_AUTH  = "https://www.instagram.com/oauth/authorize"
IG_TOKEN = "https://api.instagram.com/oauth/access_token"
IG_LONG  = "https://graph.instagram.com/access_token"
IG_ME    = "https://graph.instagram.com/me"
REDIRECT = "http://localhost:8080/callback"
SCOPES   = "instagram_business_basic,instagram_business_content_publish"

APP_ID     = os.getenv("FACEBOOK_APP_ID")     or input("Facebook App ID: ").strip()
APP_SECRET = os.getenv("FACEBOOK_APP_SECRET") or input("Facebook App Secret: ").strip()

auth_code   = None
csrf_state  = secrets.token_urlsafe(16)
server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self._respond("<h2>Instagram authorised — you can close this tab.</h2>")
            server_done.set()
        elif "error" in params:
            err = params.get("error_description", params.get("error", ["unknown"]))[0]
            self._respond(f"<h2>Error: {err}</h2>")
            server_done.set()
        else:
            self._respond("<h2>Waiting…</h2>")

    def _respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def exchange_code(code: str) -> tuple[str, str]:
    """Exchange auth code for short-lived token. Returns (access_token, user_id)."""
    resp = requests.post(IG_TOKEN, data={
        "client_id":     APP_ID,
        "client_secret": APP_SECRET,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT,
        "code":          code,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], str(data.get("user_id", ""))


def exchange_long_lived(short_token: str) -> str:
    """Exchange short-lived token for 60-day long-lived token."""
    resp = requests.get(IG_LONG, params={
        "grant_type":    "ig_exchange_token",
        "client_secret": APP_SECRET,
        "access_token":  short_token,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_user(token: str, user_id: str) -> dict:
    url = f"{IG_ME}/{user_id}" if user_id else IG_ME
    resp = requests.get(url, params={
        "fields":       "id,username,account_type",
        "access_token": token,
    }, timeout=10)
    if not resp.ok:
        # Fallback: just return what we have
        return {"id": user_id, "username": "", "account_type": ""}
    return resp.json()


def main():
    params = urlencode({
        "client_id":     APP_ID,
        "redirect_uri":  REDIRECT,
        "scope":         SCOPES,
        "response_type": "code",
        "state":         csrf_state,
    })
    auth_url = f"{IG_AUTH}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for Instagram login…")
    print(f"\n  {auth_url}\n")
    print("NOTE: If your app shows 'Invalid platform app', add the Instagram product")
    print("      in Facebook Developer Console → your app → Add Product → Instagram,")
    print("      then add http://localhost:8080/callback as a Valid OAuth Redirect URI.")
    print()
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Exchanging for tokens…")
    short_token, user_id = exchange_code(auth_code)
    long_token           = exchange_long_lived(short_token)
    user                 = get_user(long_token, user_id)

    ig_id     = user.get("id") or user_id
    username  = user.get("username", "")
    acct_type = user.get("account_type", "")

    print(f"\nConnected: @{username} ({acct_type}) — ID: {ig_id}")

    if acct_type.upper() == "PERSONAL":
        print("\nWarning: this is a Personal account.")
        print("Switch to Business or Creator in Instagram → Settings → Account type and tools.")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "INSTAGRAM_ACCOUNT_ID",   ig_id)
    set_key(str(ENV_FILE), "INSTAGRAM_ACCESS_TOKEN",  long_token)

    print(f"\nSaved to {ENV_FILE}")
    print("\nNow go to Accounts → Instagram → Verify in the Socialline app.")
    print("Or click 'I authorized — save my account' in the browser.")


if __name__ == "__main__":
    main()
