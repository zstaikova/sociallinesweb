#!/usr/bin/env python3
"""
Instagram setup — finds the Instagram Business Account linked to your Facebook Page.
Prerequisites: FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN must be in .env
Run once: python bin/setup/instagram.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
import requests
from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

GRAPH = "https://graph.facebook.com/v19.0"

PAGE_ID    = os.getenv("FACEBOOK_PAGE_ID")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

if not PAGE_ID or not PAGE_TOKEN:
    print("Missing FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN in .env")
    print("Run bin/auth/facebook.py first.")
    sys.exit(1)


def main():
    print("Looking for Instagram Business Account linked to your Facebook Page...")

    resp = requests.get(f"{GRAPH}/{PAGE_ID}", params={
        "fields": "instagram_business_account", "access_token": PAGE_TOKEN,
    }, timeout=10)
    resp.raise_for_status()

    ig = resp.json().get("instagram_business_account")
    if not ig:
        print("\nNo Instagram Business Account found.")
        print("Make sure your Instagram is a Business/Creator account connected to your Facebook Page.")
        sys.exit(1)

    ig_id = ig["id"]

    resp = requests.get(f"{GRAPH}/{ig_id}", params={
        "fields": "id,username,name", "access_token": PAGE_TOKEN,
    }, timeout=10)
    if not resp.ok:
        print(f"Found account ID {ig_id} but could not verify it.")
        sys.exit(1)

    info = resp.json()
    username = info.get("username") or info.get("name") or "(unknown)"
    print(f"Found: @{username}  (id: {ig_id})")

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "INSTAGRAM_ACCOUNT_ID", ig_id)
    print(f"Saved INSTAGRAM_ACCOUNT_ID to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth instagram")


if __name__ == "__main__":
    main()
