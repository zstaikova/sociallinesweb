#!/usr/bin/env python3
"""
Facebook OAuth flow — opens browser, catches callback, saves permanent token to .env
Run once: python bin/auth/facebook.py
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

GRAPH    = "https://graph.facebook.com/v19.0"
REDIRECT = "http://localhost:8080/callback"
SCOPES   = "pages_manage_posts,pages_read_engagement,pages_show_list,public_profile,instagram_basic,instagram_content_publish,business_management"

APP_ID     = os.getenv("FACEBOOK_APP_ID") or input("App ID: ").strip()
APP_SECRET = os.getenv("FACEBOOK_APP_SECRET") or input("App Secret: ").strip()

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
            self._respond(f"<h2>Error: {params.get('error_description', ['unknown'])[0]}</h2>")
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
    resp = requests.get(f"{GRAPH}/oauth/access_token", params={
        "client_id": APP_ID, "redirect_uri": REDIRECT,
        "client_secret": APP_SECRET, "code": code,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def exchange_long_lived(short_token):
    resp = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": APP_ID,
        "client_secret": APP_SECRET, "fb_exchange_token": short_token,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_pages(long_token):
    resp = requests.get(f"{GRAPH}/me/accounts", params={
        "fields": "id,name,access_token,instagram_business_account", "access_token": long_token,
    })
    resp.raise_for_status()
    return resp.json().get("data", [])


def main():
    params = urlencode({
        "client_id": APP_ID, "redirect_uri": REDIRECT,
        "scope": SCOPES, "response_type": "code",
    })
    auth_url = f"https://www.facebook.com/dialog/oauth?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for Facebook login...")
    print(f"\n  {auth_url}\n")
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Auth code received. Exchanging for tokens...")
    short_token = exchange_code(auth_code)
    long_token  = exchange_long_lived(short_token)
    pages       = get_pages(long_token)

    if not pages:
        print("\nNo pages found. Create one at: https://www.facebook.com/pages/create")
        sys.exit(1)

    print(f"\nFound {len(pages)} page(s):")
    for i, p in enumerate(pages):
        print(f"  [{i}] {p['name']}  (id: {p['id']})")

    idx = 0 if len(pages) == 1 else int(input("\nWhich page? Enter number: "))
    chosen = pages[idx]

    resp = requests.get(f"{GRAPH}/{chosen['id']}", params={
        "fields": "name,id", "access_token": chosen["access_token"],
    })
    if not resp.ok:
        print(f"Token verification failed: {resp.text}")
        sys.exit(1)

    print(f"\nVerified: {resp.json()['name']}")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "FACEBOOK_APP_ID",            APP_ID)
    set_key(str(ENV_FILE), "FACEBOOK_APP_SECRET",        APP_SECRET)
    set_key(str(ENV_FILE), "FACEBOOK_PAGE_ID",           chosen["id"])
    set_key(str(ENV_FILE), "FACEBOOK_PAGE_ACCESS_TOKEN", chosen["access_token"])

    ig = chosen.get("instagram_business_account")
    if ig:
        set_key(str(ENV_FILE), "INSTAGRAM_ACCOUNT_ID", ig["id"])
        print(f"\nInstagram Business account detected: {ig['id']}")
        print("INSTAGRAM_ACCOUNT_ID saved — no need to run Instagram setup separately.")

    print(f"\nSaved to {ENV_FILE}")
    print("\nSetup complete. Run: python bin/cli.py auth facebook")


if __name__ == "__main__":
    main()
