#!/usr/bin/env python3
"""
Instagram OAuth flow — uses the Instagram app (socialline-IG) credentials.
Run once: python bin/auth/instagram.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
import time
import webbrowser
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

IG_AUTH_URL  = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH     = "https://graph.instagram.com"
REDIRECT     = "http://localhost:8080/callback"
SCOPES       = "instagram_business_basic,instagram_business_content_publish"

APP_ID     = os.getenv("INSTAGRAM_APP_ID") or input("Instagram App ID: ").strip()
APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET") or input("Instagram App Secret: ").strip()

auth_code   = None
server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self._respond("<h2>Done — you can close this tab.</h2>")
            server_done.set()
        elif "error" in params:
            desc = params.get("error_description", ["unknown"])[0]
            self._respond(f"<h2>Error: {desc}</h2>")
            server_done.set()
        else:
            self._respond("<h2>Waiting...</h2>")

    def _respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def exchange_code(code):
    resp = requests.post(IG_TOKEN_URL, data={
        "client_id": APP_ID, "client_secret": APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code": code,
    })
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data["user_id"]


def exchange_long_lived(short_token):
    resp = requests.get(f"{IG_GRAPH}/access_token", params={
        "grant_type": "ig_exchange_token", "client_id": APP_ID,
        "client_secret": APP_SECRET, "access_token": short_token,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def main():
    params = urlencode({
        "client_id": APP_ID, "redirect_uri": REDIRECT,
        "scope": SCOPES, "response_type": "code",
    })
    auth_url = f"{IG_AUTH_URL}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for Instagram login...")
    print(f"\n  {auth_url}\n")
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Auth code received. Exchanging for tokens...")
    short_token, ig_user_id = exchange_code(auth_code)
    long_token = exchange_long_lived(short_token)

    resp = requests.get(f"{IG_GRAPH}/v21.0/{ig_user_id}", params={
        "fields": "id,username,name", "access_token": long_token,
    })
    if resp.ok:
        info = resp.json()
        handle = info.get("username") or info.get("name")
        print(f"Instagram account verified: @{handle}  (id: {ig_user_id})")
    else:
        print(f"Warning: could not verify account (id: {ig_user_id})")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "INSTAGRAM_APP_ID",      APP_ID)
    set_key(str(ENV_FILE), "INSTAGRAM_APP_SECRET",   APP_SECRET)
    set_key(str(ENV_FILE), "INSTAGRAM_ACCOUNT_ID",   str(ig_user_id))
    set_key(str(ENV_FILE), "INSTAGRAM_ACCESS_TOKEN", long_token)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth instagram")


if __name__ == "__main__":
    main()
