#!/usr/bin/env python3
"""
TikTok OAuth flow — opens browser, catches callback automatically.
Run once: python bin/auth/tiktok.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
import secrets
import hashlib
import base64
import webbrowser
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv, set_key
from bin.auth._callback_server import wait_for_callback

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API_BASE  = "https://open.tiktokapis.com/v2"
REDIRECT  = "https://famjammemes.github.io/famjammemes/"
SCOPES    = "user.info.basic,video.publish,video.upload"

CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY") or input("TikTok Client Key: ").strip()
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET") or input("TikTok Client Secret: ").strip()

csrf_state     = secrets.token_urlsafe(16)
code_verifier  = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b"=").decode()


def exchange_code(code):
    resp = requests.post(TOKEN_URL, data={
        "client_key": CLIENT_KEY, "client_secret": CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code_verifier": code_verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token exchange failed: {data}")
    return data["access_token"], data["open_id"], data.get("refresh_token", "")


def main():
    params = urlencode({
        "client_key": CLIENT_KEY, "response_type": "code",
        "scope": SCOPES, "redirect_uri": REDIRECT,
        "state": csrf_state, "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_URL}?{params}"

    print("\nOpening browser for TikTok login...")
    print(f"\n  {auth_url}\n")
    webbrowser.open(auth_url)
    print("Waiting for callback (the browser will redirect automatically)...")

    result = wait_for_callback()

    if "error" in result:
        print(f"Auth error: {result.get('error_description', result['error'])}")
        sys.exit(1)
    if "code" not in result:
        print("No auth code received — timed out.")
        sys.exit(1)

    state = result.get("state", "")
    if state != csrf_state:
        print("Warning: CSRF state mismatch — continuing anyway.")

    print("Exchanging code for tokens...")
    access_token, open_id, refresh_token = exchange_code(result["code"])

    resp = requests.get(f"{API_BASE}/user/info/",
        params={"fields": "display_name,avatar_url"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.ok:
        user = resp.json().get("data", {}).get("user", {})
        print(f"TikTok account verified: {user.get('display_name')} (open_id: {open_id})")
    else:
        print(f"Warning: could not verify user (open_id: {open_id})")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "TIKTOK_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_FILE), "TIKTOK_REFRESH_TOKEN", refresh_token)
    set_key(str(ENV_FILE), "TIKTOK_OPEN_ID",       open_id)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth tiktok")


if __name__ == "__main__":
    main()
