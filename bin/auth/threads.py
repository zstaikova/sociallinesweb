#!/usr/bin/env python3
"""
Threads OAuth flow — opens browser, catches callback, saves token to .env
Run once: python bin/auth/threads.py
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

THREADS_AUTH  = "https://threads.net/oauth/authorize"
THREADS_TOKEN = "https://graph.threads.net/oauth/access_token"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
REDIRECT      = "http://localhost:8080/callback"
SCOPES        = "threads_basic,threads_content_publish"

APP_ID     = os.getenv("THREADS_APP_ID") or input("Threads App ID: ").strip()
APP_SECRET = os.getenv("THREADS_APP_SECRET") or input("Threads App Secret: ").strip()

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
    resp = requests.post(THREADS_TOKEN, data={
        "client_id": APP_ID, "client_secret": APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code": code,
    })
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], str(data["user_id"])


def exchange_long_lived(short_token):
    resp = requests.get(f"{THREADS_GRAPH}/access_token", params={
        "grant_type": "th_exchange_token", "client_secret": APP_SECRET,
        "access_token": short_token,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def main():
    params = urlencode({
        "client_id": APP_ID, "redirect_uri": REDIRECT,
        "scope": SCOPES, "response_type": "code",
    })
    auth_url = f"{THREADS_AUTH}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for Threads login...")
    print(f"\n  {auth_url}\n")
    time.sleep(1)
    webbrowser.open(auth_url)

    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("No auth code received.")
        sys.exit(1)

    print("Exchanging for tokens...")
    short_token, user_id = exchange_code(auth_code)
    long_token = exchange_long_lived(short_token)

    resp = requests.get(f"{THREADS_GRAPH}/{user_id}",
        params={"fields": "id,username,name", "access_token": long_token})
    if resp.ok:
        info = resp.json()
        print(f"Threads account verified: @{info.get('username', info.get('name'))} (id: {user_id})")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "THREADS_APP_ID",      APP_ID)
    set_key(str(ENV_FILE), "THREADS_APP_SECRET",   APP_SECRET)
    set_key(str(ENV_FILE), "THREADS_USER_ID",      user_id)
    set_key(str(ENV_FILE), "THREADS_ACCESS_TOKEN", long_token)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth threads")


if __name__ == "__main__":
    main()
