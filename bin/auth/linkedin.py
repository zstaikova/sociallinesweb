#!/usr/bin/env python3
"""
LinkedIn OAuth flow — opens browser, catches callback, saves token to .env
Run once: python bin/auth/linkedin.py
Requires LinkedIn app with scope: w_member_social, openid, profile
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

LI_AUTH  = "https://www.linkedin.com/oauth/v2/authorization"
LI_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
REDIRECT = "http://localhost:8080/callback"
SCOPES   = "openid profile w_member_social r_basicprofile"

CLIENT_ID     = os.getenv("LINKEDIN_CLIENT_ID") or input("LinkedIn Client ID: ").strip()
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET") or input("LinkedIn Client Secret: ").strip()

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
    resp = requests.post(LI_TOKEN, data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    })
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("refresh_token", "")


def get_person_urn(token):
    resp = requests.get("https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return f"urn:li:person:{data['sub']}", data.get("name", "")


def main():
    params = urlencode({
        "response_type": "code", "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT, "state": csrf_state, "scope": SCOPES,
    })
    auth_url = f"{LI_AUTH}?{params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    print("\nOpening browser for LinkedIn login...")
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
    person_urn, name = get_person_urn(token)
    print(f"LinkedIn verified: {name} ({person_urn})")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "LINKEDIN_CLIENT_ID",     CLIENT_ID)
    set_key(str(ENV_FILE), "LINKEDIN_CLIENT_SECRET",  CLIENT_SECRET)
    set_key(str(ENV_FILE), "LINKEDIN_ACCESS_TOKEN",   token)
    set_key(str(ENV_FILE), "LINKEDIN_PERSON_URN",     person_urn)
    if refresh_token:
        set_key(str(ENV_FILE), "LINKEDIN_REFRESH_TOKEN", refresh_token)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth linkedin")


if __name__ == "__main__":
    main()
