#!/usr/bin/env python3
import sys
import copy
import os
import subprocess
import threading
from pathlib import Path

# Force UTF-8 output on Windows so emoji in captions don't crash print() calls
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv, set_key
load_dotenv(ROOT / ".env")

from flask import Flask, render_template, request, jsonify, send_from_directory

ENV_FILE = ROOT / ".env"

# ── Platform definitions for setup wizard ─────────────────────
SETUP_PLATFORMS = [
    {
        "id": "reddit", "name": "Reddit", "icon": "bi-reddit", "color": "#ff4500",
        "note": "Content source — required to pull memes from Reddit",
        "type": "manual",
        "keys_required": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
        "form_fields": [
            {"key": "REDDIT_CLIENT_ID",     "label": "Client ID",     "help": "Found under your app name at reddit.com/prefs/apps"},
            {"key": "REDDIT_CLIENT_SECRET", "label": "Client Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://www.reddit.com/prefs/apps"),
            ("Click 'create another app' → choose", "script type"),
            ("Set redirect URI to", "http://localhost:8080"),
            ("Copy Client ID (under app name) and Secret", ""),
        ],
        "auth_script": None,
        "verify_cmd": "reddit",
    },
    {
        "id": "facebook", "name": "Facebook", "icon": "bi-facebook", "color": "#1877f2",
        "note": "Required for Facebook posts + image hosting for Instagram & Threads",
        "type": "oauth",
        "keys_required": ["FACEBOOK_PAGE_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "FACEBOOK_APP_ID",     "label": "App ID"},
            {"key": "FACEBOOK_APP_SECRET", "label": "App Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://developers.facebook.com → My Apps → Create App"),
            ("Choose Business type, add Pages product", ""),
            ("Get App ID and Secret from Basic Settings", ""),
            ("Enter them below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/facebook.py",
        "verify_cmd": "facebook",
    },
    {
        "id": "instagram", "name": "Instagram", "icon": "bi-instagram", "color": "#e1306c",
        "note": "Requires Facebook to be configured first",
        "type": "oauth",
        "keys_required": ["INSTAGRAM_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "INSTAGRAM_APP_ID",     "label": "Instagram App ID", "help": "From a separate Meta app with Instagram permissions"},
            {"key": "INSTAGRAM_APP_SECRET", "label": "App Secret", "type": "password"},
        ],
        "steps": [
            ("Create a separate Meta app for Instagram", "https://developers.facebook.com"),
            ("Add Instagram product, enable", "instagram_business_content_publish"),
            ("Enter App ID and Secret below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/instagram.py",
        "verify_cmd": "instagram",
    },
    {
        "id": "threads", "name": "Threads", "icon": "bi-threads", "color": "#000000",
        "note": "Requires Facebook to be configured first",
        "type": "oauth",
        "keys_required": ["THREADS_ACCESS_TOKEN", "THREADS_USER_ID"],
        "prereq_fields": [
            {"key": "THREADS_APP_ID",     "label": "Threads App ID"},
            {"key": "THREADS_APP_SECRET", "label": "App Secret", "type": "password"},
            {"key": "THREADS_USER_ID",    "label": "Threads User ID", "help": "Your numeric Threads user ID — found after completing OAuth"},
        ],
        "steps": [
            ("Create a Meta app with Threads permissions", "https://developers.facebook.com"),
            ("Add threads_basic and threads_content_publish scopes", ""),
            ("Enter App ID and Secret below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/threads.py",
        "verify_cmd": "threads",
    },
    {
        "id": "bluesky", "name": "Bluesky", "icon": "bi-cloud", "color": "#0085ff",
        "note": "Easiest to set up — no app review needed",
        "type": "manual",
        "keys_required": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
        "form_fields": [
            {"key": "BLUESKY_HANDLE",       "label": "Handle",       "placeholder": "yourname.bsky.social"},
            {"key": "BLUESKY_APP_PASSWORD", "label": "App Password", "type": "password",
             "help": "Generate at bsky.app/settings/app-passwords"},
        ],
        "steps": [
            ("Go to", "https://bsky.app/settings/app-passwords"),
            ("Click Add App Password, name it 'sociallines'", ""),
            ("Copy the password (xxxx-xxxx-xxxx-xxxx format)", ""),
        ],
        "auth_script": None,
        "verify_cmd": "bluesky",
    },
    {
        "id": "linkedin", "name": "LinkedIn", "icon": "bi-linkedin", "color": "#0a66c2",
        "note": "Posts to your personal LinkedIn profile",
        "type": "oauth",
        "keys_required": ["LINKEDIN_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "LINKEDIN_CLIENT_ID",     "label": "Client ID"},
            {"key": "LINKEDIN_CLIENT_SECRET", "label": "Client Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://www.linkedin.com/developers/apps → Create App"),
            ("Add OAuth scopes:", "w_member_social, openid, profile"),
            ("Set redirect URI to", "http://localhost:8080/callback"),
            ("Enter credentials below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/linkedin.py",
        "verify_cmd": "linkedin",
    },
    {
        "id": "pinterest", "name": "Pinterest", "icon": "bi-pinterest", "color": "#e60023",
        "note": "Posts to a Pinterest board of your choice",
        "type": "oauth",
        "keys_required": ["PINTEREST_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "PINTEREST_CLIENT_ID",     "label": "App ID"},
            {"key": "PINTEREST_CLIENT_SECRET", "label": "App Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://developers.pinterest.com → My Apps → Create App"),
            ("Add scopes:", "boards:read, pins:write, user_accounts:read"),
            ("Set redirect URI to", "http://localhost:8080/callback"),
            ("Enter credentials below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/pinterest.py",
        "verify_cmd": "pinterest",
    },
    {
        "id": "tiktok", "name": "TikTok", "icon": "bi-tiktok", "color": "#010101",
        "note": "Requires TikTok app certification (Content Posting API)",
        "type": "oauth",
        "keys_required": ["TIKTOK_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "TIKTOK_CLIENT_KEY",    "label": "Client Key"},
            {"key": "TIKTOK_CLIENT_SECRET", "label": "Client Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://developers.tiktok.com → Manage Apps → Create App"),
            ("Apply for Content Posting API (requires review)", ""),
            ("Enter credentials below, then click Launch Auth after approval", ""),
        ],
        "auth_script": "bin/auth/tiktok.py",
        "verify_cmd": "tiktok",
    },
    {
        "id": "x", "name": "X (Twitter)", "icon": "bi-twitter-x", "color": "#000000",
        "note": "Currently requires paid API credits — disabled by default",
        "type": "manual",
        "keys_required": ["X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
        "form_fields": [
            {"key": "X_CONSUMER_KEY",        "label": "Consumer Key"},
            {"key": "X_CONSUMER_SECRET",     "label": "Consumer Secret",      "type": "password"},
            {"key": "X_ACCESS_TOKEN",        "label": "Access Token"},
            {"key": "X_ACCESS_TOKEN_SECRET", "label": "Access Token Secret",  "type": "password"},
        ],
        "steps": [
            ("Go to", "https://developer.twitter.com → your app → Settings"),
            ("Set permissions to Read and Write, regenerate Access Token", ""),
            ("Add API credits to your developer account to enable posting", ""),
        ],
        "auth_script": None,
        "verify_cmd": "x",
    },
]

_oauth_processes = {}  # platform_id → subprocess


def _platform_status(p):
    load_dotenv(ENV_FILE, override=True)
    return all(os.getenv(k) for k in p["keys_required"])

app = Flask(__name__, template_folder="templates", static_folder="static")

QUEUE_DIR = ROOT / "famjammemes" / "queue"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@app.route("/")
def queue():
    images = []
    if QUEUE_DIR.exists():
        images = [
            {"filename": f.name, "caption": f.stem.replace("_", " ").replace("-", " ")}
            for f in sorted(QUEUE_DIR.iterdir())
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
    return render_template("queue.html", images=images)


@app.route("/queue/<path:filename>")
def serve_queue_image(filename):
    return send_from_directory(QUEUE_DIR, filename)


@app.route("/post/<path:filename>")
def editor(filename):
    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return "Image not found", 404
    caption = image_path.stem.replace("_", " ").replace("-", " ")
    return render_template("editor.html", filename=filename, caption=caption)


@app.route("/api/generate-captions", methods=["POST"])
def api_generate_captions():
    from caption_ai import generate_platform_captions
    data = request.get_json()
    core_caption = (data or {}).get("caption", "").strip()
    if not core_caption:
        return jsonify({"error": "No caption provided"}), 400
    try:
        captions = generate_platform_captions(core_caption)
        return jsonify(captions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/publish", methods=["POST"])
def api_publish():
    from pipeline.core.content_item import ContentItem
    from pipeline.core.content_store import ContentStore
    from pipeline.brands.famjam.config import create_pipeline

    data = request.get_json()
    filename  = data.get("filename")
    captions  = data.get("captions", {})
    platforms = data.get("platforms", [])

    if not filename or not platforms:
        return jsonify({"error": "filename and platforms required"}), 400

    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return jsonify({"error": "Image not found"}), 404

    store    = ContentStore()
    pipeline = create_pipeline(platforms=platforms, store=store)

    item = ContentItem(
        source_url=f"file://{image_path.resolve()}",
        source_platform="local",
        media_path=image_path,
        caption=captions.get("facebook", image_path.stem),
        tags=["local"],
    )

    if not store.exists(item.id):
        store.save(item)

    for transformer in pipeline.shared_transformers:
        item = transformer.transform(item)

    results = {}
    for platform_config in pipeline.platforms:
        pname = platform_config.name
        try:
            import io, contextlib
            platform_item = copy.deepcopy(item)
            platform_item.caption = captions.get(pname, item.caption)

            for transformer in platform_config.transformers:
                platform_item = transformer.transform(platform_item)

            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                success = platform_config.publisher.publish(platform_item)
            output = captured.getvalue().strip()

            if success:
                post_id = platform_item.metadata.get(f"{pname}_post_id")
                store.mark_posted(item.id, pname, post_id)
                results[pname] = {"status": "posted", "post_id": post_id}
            else:
                store.mark_failed(item.id, pname)
                results[pname] = {"status": "failed", "detail": output or "unknown error"}
        except Exception as e:
            results[pname] = {"status": "error", "detail": str(e)}

    return jsonify(results)


@app.route("/setup")
def setup():
    platforms = []
    for p in SETUP_PLATFORMS:
        platforms.append({**p, "configured": _platform_status(p)})
    return render_template("setup.html", platforms=platforms)


@app.route("/setup/<platform_id>")
def setup_platform(platform_id):
    p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
    if not p:
        return "Platform not found", 404
    configured = _platform_status(p)
    # Load current values for pre-filling form fields
    load_dotenv(ENV_FILE, override=True)
    current_values = {}
    for field in p.get("form_fields", []) + p.get("prereq_fields", []):
        val = os.getenv(field["key"], "")
        current_values[field["key"]] = val
    return render_template("setup_platform.html", p=p, configured=configured, current_values=current_values)


@app.route("/api/setup/save-keys", methods=["POST"])
def api_setup_save_keys():
    data = request.get_json()
    platform_id = data.get("platform_id")
    keys = data.get("keys", {})

    if not platform_id or not keys:
        return jsonify({"error": "platform_id and keys required"}), 400

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    for k, v in keys.items():
        if v:
            set_key(str(ENV_FILE), k, v)

    load_dotenv(ENV_FILE, override=True)

    p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
    configured = _platform_status(p) if p else False
    return jsonify({"ok": True, "configured": configured})


@app.route("/api/setup/launch-oauth/<platform_id>", methods=["POST"])
def api_setup_launch_oauth(platform_id):
    p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
    if not p or not p.get("auth_script"):
        return jsonify({"error": "No auth script for this platform"}), 400

    auth_script = ROOT / p["auth_script"]
    if not auth_script.exists():
        return jsonify({"error": f"Auth script not found: {p['auth_script']}"}), 404

    # Kill any existing process for this platform
    existing = _oauth_processes.get(platform_id)
    if existing and existing.poll() is None:
        existing.terminate()

    try:
        proc = subprocess.Popen(
            [sys.executable, str(auth_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _oauth_processes[platform_id] = proc

        def _stream():
            for line in proc.stdout:
                pass  # drain so it doesn't block

        threading.Thread(target=_stream, daemon=True).start()
        return jsonify({"ok": True, "pid": proc.pid, "message": f"Auth flow started in terminal. Check your browser for the OAuth window."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/setup/status/<platform_id>")
def api_setup_status(platform_id):
    p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
    if not p:
        return jsonify({"error": "Unknown platform"}), 404
    configured = _platform_status(p)
    proc = _oauth_processes.get(platform_id)
    running = proc is not None and proc.poll() is None
    return jsonify({"configured": configured, "oauth_running": running})


@app.route("/api/setup/verify/<platform_id>")
def api_setup_verify(platform_id):
    load_dotenv(ENV_FILE, override=True)
    try:
        if platform_id == "reddit":
            import praw
            reddit = praw.Reddit(
                client_id=os.environ["REDDIT_CLIENT_ID"],
                client_secret=os.environ["REDDIT_CLIENT_SECRET"],
                user_agent="socialline/1.0",
            )
            list(reddit.subreddit("memes").hot(limit=1))
            return jsonify({"ok": True, "message": "Reddit read-only access confirmed"})

        elif platform_id == "bluesky":
            from pipeline.platforms.bluesky.publisher import BlueskyPublisher
            ok = BlueskyPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "facebook":
            from pipeline.platforms.facebook.publisher import FacebookPublisher
            ok = FacebookPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "instagram":
            from pipeline.platforms.instagram.publisher import InstagramPublisher
            ok = InstagramPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "threads":
            from pipeline.platforms.threads.publisher import ThreadsPublisher
            ok = ThreadsPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "linkedin":
            from pipeline.platforms.linkedin.publisher import LinkedInPublisher
            ok = LinkedInPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "pinterest":
            from pipeline.platforms.pinterest.publisher import PinterestPublisher
            ok = PinterestPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "tiktok":
            from pipeline.platforms.tiktok.publisher import TikTokPublisher
            ok = TikTokPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        elif platform_id == "x":
            from pipeline.platforms.x.publisher import XPublisher
            ok = XPublisher().verify_auth()
            return jsonify({"ok": ok, "message": "Auth OK" if ok else "Auth failed"})

        else:
            return jsonify({"error": "Unknown platform"}), 404

    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    def _restart():
        import time
        time.sleep(0.4)  # let the response go out first
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"Queue folder: {QUEUE_DIR}")
    print("Starting FamJam posting UI at http://localhost:5000")
    app.run(debug=True, port=5000)
