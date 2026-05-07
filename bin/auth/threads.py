#!/usr/bin/env python3
"""
Threads OAuth flow — opens browser, catches callback automatically.
Run once: python bin/auth/threads.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
import webbrowser
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv, set_key
from bin.auth._callback_server import wait_for_callback

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

THREADS_AUTH  = "https://threads.net/oauth/authorize"
THREADS_TOKEN = "https://graph.threads.net/oauth/access_token"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
REDIRECT      = "https://app.socialline.space/callback"
SCOPES        = "threads_basic,threads_content_publish"

APP_ID     = os.getenv("THREADS_APP_ID") or input("Threads App ID: ").strip()
APP_SECRET = os.getenv("THREADS_APP_SECRET") or input("Threads App Secret: ").strip()


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

    print("\nOpening browser for Threads login...")
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

    print("Exchanging for tokens...")
    short_token, user_id = exchange_code(result["code"])
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
