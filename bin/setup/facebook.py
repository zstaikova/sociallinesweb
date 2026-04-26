#!/usr/bin/env python3
"""
Facebook one-time setup — exchanges a short-lived token for a permanent Page Access Token.
Run once: python bin/setup/facebook.py
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


def exchange_for_long_lived(app_id, app_secret, short_token):
    resp = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": short_token,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_pages(long_lived_token):
    resp = requests.get(f"{GRAPH}/me/accounts", params={
        "access_token": long_lived_token, "fields": "id,name,access_token",
    })
    resp.raise_for_status()
    return resp.json().get("data", [])


def main():
    print("=" * 55)
    print("  Facebook Page Access Token Setup")
    print("=" * 55)

    app_id     = os.getenv("FACEBOOK_APP_ID") or input("\nPaste your App ID: ").strip()
    app_secret = os.getenv("FACEBOOK_APP_SECRET") or input("Paste your App Secret: ").strip()

    print("\nGet a short-lived token at: https://developers.facebook.com/tools/explorer")
    print("Required permissions: pages_manage_posts, pages_read_engagement, pages_show_list\n")

    short_token = input("Paste short-lived token: ").strip()

    print("\nExchanging for long-lived token...")
    try:
        long_token = exchange_for_long_lived(app_id, app_secret, short_token)
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)

    print("Fetching pages...")
    try:
        pages = get_pages(long_token)
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)

    if not pages:
        print("No pages found.")
        sys.exit(1)

    print(f"\nFound {len(pages)} page(s):")
    for i, page in enumerate(pages):
        print(f"  [{i}] {page['name']}  (id: {page['id']})")

    idx = 0 if len(pages) == 1 else int(input("\nWhich page? Enter number: "))
    chosen = pages[idx]

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "FACEBOOK_APP_ID",            app_id)
    set_key(str(ENV_FILE), "FACEBOOK_APP_SECRET",        app_secret)
    set_key(str(ENV_FILE), "FACEBOOK_PAGE_ID",           chosen["id"])
    set_key(str(ENV_FILE), "FACEBOOK_PAGE_ACCESS_TOKEN", chosen["access_token"])

    print(f"\nSaved to {ENV_FILE}")

    resp = requests.get(f"{GRAPH}/{chosen['id']}", params={
        "fields": "name,id", "access_token": chosen["access_token"],
    })
    if resp.ok:
        data = resp.json()
        print(f"Auth OK — page: {data['name']} ({data['id']})")
        print("\nRun: python bin/cli.py auth facebook")
    else:
        print(f"Verification failed: {resp.text}")


if __name__ == "__main__":
    main()
