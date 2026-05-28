#!/usr/bin/env python3
"""
Pinterest OAuth flow — opens browser, catches callback, saves token to .env
Run once: python bin/auth/pinterest.py
Requires Pinterest app with scope: boards:read, pins:write
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

PI_AUTH  = "https://www.pinterest.com/oauth/"
PI_TOKEN = "https://api.pinterest.com/v5/oauth/token"
PI_API   = "https://api.pinterest.com/v5"
REDIRECT = "http://localhost:8080/callback"
SCOPES   = "boards:read,pins:write,user_accounts:read"

CLIENT_ID     = os.getenv("PINTEREST_CLIENT_ID") or input("Pinterest App ID: ").strip()
CLIENT_SECRET = os.getenv("PINTEREST_CLIENT_SECRET") or input("Pinterest App Secret: ").strip()

auth_code   = None
csrf_state  = secrets.token_urlsafe(16)
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
    resp = requests.post(PI_TOKEN,
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("refresh_token", "")


def get_boards(token):
    resp = requests.get(f"{PI_API}/boards",
        headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("items", [])


def main():
    params = urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "response_type": "code", "scope": SCOPES, "state": csrf_state,
    })
    auth_url = f"{PI_AUTH}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for Pinterest login...")
    print(f"\n  {auth_url}\n")
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Exchanging for token...")
    token, refresh_token = exchange_code(auth_code)

    boards = get_boards(token)
    if not boards:
        print("No boards found. Create a board on Pinterest first.")
        sys.exit(1)

    print(f"\nFound {len(boards)} board(s):")
    for i, b in enumerate(boards):
        print(f"  [{i}] {b['name']}  (id: {b['id']})")

    idx = 0 if len(boards) == 1 else int(input("\nWhich board to post to? Enter number: "))
    board_id = boards[idx]["id"]

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "PINTEREST_CLIENT_ID",     CLIENT_ID)
    set_key(str(ENV_FILE), "PINTEREST_CLIENT_SECRET",  CLIENT_SECRET)
    set_key(str(ENV_FILE), "PINTEREST_ACCESS_TOKEN",   token)
    set_key(str(ENV_FILE), "PINTEREST_BOARD_ID",       board_id)
    if refresh_token:
        set_key(str(ENV_FILE), "PINTEREST_REFRESH_TOKEN", refresh_token)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth pinterest")


if __name__ == "__main__":
    main()
