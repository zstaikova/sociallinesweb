#!/usr/bin/env python3
"""
Sociallines Setup Wizard
Run: python bin/setup.py
"""
import sys
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv, set_key

ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

# ── ANSI colors ────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def clr(text, *codes):
    return "".join(codes) + text + RESET

def clear():
    os.system("cls" if os.name == "nt" else "clear")

# ── Platform definitions ───────────────────────────────────────
PLATFORMS = [
    {
        "id":       "reddit",
        "name":     "Reddit (Source)",
        "icon":     "📰",
        "keys":     ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
        "auth":     "bin/auth/reddit.py",
        "note":     "Required to pull content from Reddit",
        "steps": [
            "Go to https://www.reddit.com/prefs/apps",
            "Click 'create another app' → choose 'script'",
            "Set redirect URI to http://localhost:8080",
            "Copy Client ID (under app name) and Secret",
        ],
        "manual_keys": [
            ("REDDIT_CLIENT_ID",     "Reddit Client ID"),
            ("REDDIT_CLIENT_SECRET", "Reddit Client Secret"),
        ],
    },
    {
        "id":    "facebook",
        "name":  "Facebook",
        "icon":  "📘",
        "keys":  ["FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
        "auth":  "bin/auth/facebook.py",
        "note":  "Required for Facebook + Instagram + Threads image hosting",
        "steps": [
            "Go to https://developers.facebook.com → My Apps → Create App",
            "Choose Business type, add 'Pages' product",
            "Get App ID and App Secret from Basic Settings",
            "Run the auth script to complete OAuth",
        ],
    },
    {
        "id":    "instagram",
        "name":  "Instagram",
        "icon":  "📸",
        "keys":  ["INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN"],
        "auth":  "bin/auth/instagram.py",
        "note":  "Requires Facebook to be set up first",
        "steps": [
            "Create a separate Meta app for Instagram (type: Business)",
            "Add 'Instagram' product, enable instagram_business_content_publish",
            "Run the auth script to get your token",
        ],
    },
    {
        "id":    "threads",
        "name":  "Threads",
        "icon":  "🧵",
        "keys":  ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN"],
        "auth":  "bin/auth/threads.py",
        "note":  "Requires Facebook to be set up first (uses FB CDN for images)",
        "steps": [
            "Create a Meta app with Threads permissions",
            "Go to https://developers.facebook.com → Threads API",
            "Add threads_basic and threads_content_publish scopes",
            "Run the auth script to complete OAuth",
        ],
    },
    {
        "id":    "bluesky",
        "name":  "Bluesky",
        "icon":  "🦋",
        "keys":  ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
        "auth":  "bin/auth/bluesky.py",
        "note":  "Easiest to set up — no app review needed",
        "steps": [
            "Go to https://bsky.app/settings/app-passwords",
            "Click 'Add App Password', give it a name (e.g. sociallines)",
            "Copy the generated password (xxxx-xxxx-xxxx-xxxx format)",
            "Run the auth script and enter your handle + app password",
        ],
    },
    {
        "id":    "linkedin",
        "name":  "LinkedIn",
        "icon":  "💼",
        "keys":  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"],
        "auth":  "bin/auth/linkedin.py",
        "note":  "Posts to your personal LinkedIn profile",
        "steps": [
            "Go to https://www.linkedin.com/developers/apps → Create App",
            "Add OAuth 2.0 scope: w_member_social, openid, profile",
            "Set redirect URI to http://localhost:8080/callback",
            "Run the auth script to complete OAuth",
        ],
    },
    {
        "id":    "pinterest",
        "name":  "Pinterest",
        "icon":  "📌",
        "keys":  ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID"],
        "auth":  "bin/auth/pinterest.py",
        "note":  "Posts to a Pinterest board of your choice",
        "steps": [
            "Go to https://developers.pinterest.com → My Apps → Create App",
            "Add scopes: boards:read, pins:write, user_accounts:read",
            "Set redirect URI to http://localhost:8080/callback",
            "Run the auth script — you'll pick your board during setup",
        ],
    },
    {
        "id":    "tiktok",
        "name":  "TikTok",
        "icon":  "🎵",
        "keys":  ["TIKTOK_CLIENT_KEY", "TIKTOK_ACCESS_TOKEN"],
        "auth":  "bin/auth/tiktok.py",
        "note":  "Requires TikTok app certification (Content Posting API)",
        "steps": [
            "Go to https://developers.tiktok.com → Manage Apps → Create App",
            "Apply for Content Posting API (requires review/certification)",
            "Run the auth script after certification is approved",
        ],
    },
    {
        "id":    "x",
        "name":  "X (Twitter)",
        "icon":  "𝕏",
        "keys":  ["X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
        "auth":  None,
        "note":  "Currently disabled — API credits required on free tier",
        "steps": [
            "Go to https://developer.twitter.com → Projects & Apps",
            "Set app permissions to Read and Write",
            "Regenerate Access Token and Secret",
            "Add credits to your developer account to enable posting",
        ],
    },
]


def is_configured(platform: dict) -> bool:
    load_dotenv(ENV_FILE, override=True)
    return all(os.getenv(k) for k in platform["keys"])


def print_header():
    print(clr("""
  ╔══════════════════════════════════════╗
  ║     SOCIALLINES  Setup Wizard        ║
  ╚══════════════════════════════════════╝
""", CYAN, BOLD))


def print_platform_menu():
    print(clr("  Platforms\n", BOLD))
    for i, p in enumerate(PLATFORMS, 1):
        configured = is_configured(p)
        if configured:
            status = clr("✓ configured", GREEN)
        else:
            status = clr("○ not set up", DIM)
        print(f"  [{i}] {p['icon']}  {p['name']:<18} {status}")
    print()
    print(f"  [Q] Quit\n")


def show_platform_setup(p: dict):
    clear()
    print_header()
    configured = is_configured(p)

    print(clr(f"  {p['icon']}  {p['name']}\n", BOLD))
    print(f"  {p['note']}\n")

    if configured:
        print(clr("  ✓ Already configured\n", GREEN))
        print("  Options:")
        print("  [R] Re-run auth (refresh token)")
        print("  [B] Back\n")
        choice = input("  > ").strip().upper()
        if choice != "R":
            return
    else:
        print(clr("  Setup steps:\n", YELLOW))
        for i, step in enumerate(p["steps"], 1):
            print(f"  {i}. {step}")
        print()

        if p.get("manual_keys"):
            # Manual key entry (e.g. Reddit)
            print(clr("  Enter your credentials:\n", CYAN))
            if not ENV_FILE.exists():
                ENV_FILE.write_text("")
            for key, label in p["manual_keys"]:
                existing = os.getenv(key, "")
                if existing:
                    val = input(f"  {label} [{existing[:6]}...]: ").strip()
                    if not val:
                        val = existing
                else:
                    val = input(f"  {label}: ").strip()
                if val:
                    set_key(str(ENV_FILE), key, val)
                    load_dotenv(ENV_FILE, override=True)
            print(clr("\n  ✓ Saved.\n", GREEN))
            input("  Press Enter to continue...")
            return

        print("  Press Enter to open the auth flow, or B to go back.")
        choice = input("  > ").strip().upper()
        if choice == "B":
            return

    if p["auth"]:
        auth_script = ROOT / p["auth"]
        print(f"\n  Running: python {p['auth']}\n")
        result = subprocess.run([sys.executable, str(auth_script)])
        if result.returncode == 0:
            print(clr("\n  ✓ Setup complete!\n", GREEN))
        else:
            print(clr("\n  ✗ Setup did not complete. You can retry anytime.\n", YELLOW))
    else:
        print(clr("\n  This platform requires manual setup. See steps above.\n", YELLOW))

    input("  Press Enter to continue...")


def run_wizard():
    while True:
        clear()
        print_header()
        print_platform_menu()

        choice = input("  Select a platform to set up (or Q to quit): ").strip().upper()

        if choice == "Q":
            print(clr("\n  Setup saved. Run 'python bin/cli.py auth <platform>' to verify.\n", GREEN))
            break

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(PLATFORMS):
                show_platform_setup(PLATFORMS[idx])
            else:
                print(clr("  Invalid choice.\n", RED))
        except ValueError:
            print(clr("  Invalid choice.\n", RED))


if __name__ == "__main__":
    run_wizard()
