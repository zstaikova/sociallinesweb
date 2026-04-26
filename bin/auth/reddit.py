#!/usr/bin/env python3
"""
Reddit setup — saves Client ID and Secret to .env
Run once: python bin/auth/reddit.py
Create an app at: https://www.reddit.com/prefs/apps (choose 'script' type)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import os
from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)


def main():
    print("\nReddit Setup")
    print("Create a script app at: https://www.reddit.com/prefs/apps\n")

    client_id     = os.getenv("REDDIT_CLIENT_ID") or input("Client ID (under app name): ").strip()
    client_secret = input("Client Secret: ").strip() or os.getenv("REDDIT_CLIENT_SECRET", "")

    print("\nVerifying...")
    try:
        import praw
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="socialline/1.0",
        )
        list(reddit.subreddit("memes").hot(limit=1))
        print("Reddit auth OK — read-only access confirmed")
    except Exception as e:
        print(f"Verification failed: {e}")
        sys.exit(1)

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "REDDIT_CLIENT_ID",     client_id)
    set_key(str(ENV_FILE), "REDDIT_CLIENT_SECRET",  client_secret)

    print(f"\nSaved to {ENV_FILE}")


if __name__ == "__main__":
    main()
