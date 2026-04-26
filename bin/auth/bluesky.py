#!/usr/bin/env python3
"""
Bluesky setup — saves handle and app password to .env
No OAuth needed — Bluesky uses app passwords.
Run once: python bin/auth/bluesky.py
Get an app password at: https://bsky.app/settings/app-passwords
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

import os

def main():
    print("\nBluesky Setup")
    print("Get an app password at: https://bsky.app/settings/app-passwords\n")

    handle       = os.getenv("BLUESKY_HANDLE") or input("Bluesky handle (e.g. yourname.bsky.social): ").strip()
    app_password = input("App password (xxxx-xxxx-xxxx-xxxx): ").strip()

    print("\nVerifying...")
    try:
        from atproto import Client
        client = Client()
        client.login(handle, app_password)
        profile = client.get_profile(handle)
        print(f"Bluesky auth OK — @{profile.handle}")
    except Exception as e:
        print(f"Login failed: {e}")
        sys.exit(1)

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "BLUESKY_HANDLE",       handle)
    set_key(str(ENV_FILE), "BLUESKY_APP_PASSWORD",  app_password)

    print(f"\nSaved to {ENV_FILE}")
    print("\nVerify with: python bin/cli.py auth bluesky")


if __name__ == "__main__":
    main()
