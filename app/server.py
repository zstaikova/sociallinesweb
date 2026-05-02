#!/usr/bin/env python3
import sys
import copy
import json
import os
import secrets
import subprocess
import threading
from functools import wraps
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

from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect
from pipeline.core.accounts import AccountStore
from users import UserStore
from scheduler import ScheduleStore, start_scheduler

ENV_FILE   = ROOT / ".env"
DATA_DIR   = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_user_store  = UserStore(DATA_DIR / "users.db")
_sched_store = ScheduleStore(ROOT / "famjammemes" / "schedule.db")
from sources import SourceStore, PullEngine
_source_store = SourceStore(DATA_DIR / "sources.db")
_pull_engine  = None  # init after QUEUE_DIR is defined

# Per-user AccountStore cache  {username → AccountStore}
_acct_stores: dict[str, AccountStore] = {}

def _get_acct_store(username: str = None) -> AccountStore:
    """Return the shared platform credential store.

    Platform credentials (Facebook, Instagram, etc.) belong to the brand,
    not to individual users — all users share the same root accounts.enc.
    """
    if "shared" not in _acct_stores:
        _acct_stores["shared"] = AccountStore()
    return _acct_stores["shared"]

# Keep a module-level alias so existing code that hasn't been updated yet still works
_acct_store = None  # replaced by _get_acct_store() calls below


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _ensure_secret_key():
    """Generate and persist a Flask secret key if not already set."""
    key = os.getenv("APP_SECRET_KEY")
    if not key:
        key = secrets.token_hex(32)
        if not ENV_FILE.exists():
            ENV_FILE.write_text("")
        set_key(str(ENV_FILE), "APP_SECRET_KEY", key)
        load_dotenv(ENV_FILE, override=True)
    return key


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            next_path = request.path if request.path not in ("/", "/queue") else None
            if next_path:
                return redirect(f"/login?next={next_path}")
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(f"/login?next={request.path}")
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Admin access required"}), 403
            return render_template("403.html"), 403
        return f(*args, **kwargs)
    return decorated


def not_reviewer(f):
    """Allow admin and user roles, but block reviewer."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(f"/login?next={request.path}")
        if session.get("role") == "reviewer":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not available for reviewer accounts"}), 403
            return render_template("403.html"), 403
        return f(*args, **kwargs)
    return decorated

# ── Platform definitions for setup wizard ─────────────────────
SETUP_PLATFORMS = [
    {
        "id": "reddit", "name": "Reddit", "icon": "bi-reddit", "color": "#ff4500",
        "note": "Content source — required to pull memes from Reddit",
        "type": "manual",
        "keys_required": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
        "form_fields": [
            {"key": "REDDIT_CLIENT_ID",     "label": "Client ID",     "help": "Found under your app name at reddit.com/prefs/apps"},
            {"key": "REDDIT_CLIENT_SECRET", "label": "Password", "type": "password"},
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
        "note": "Uses your Facebook app — connect Facebook first",
        "type": "derived",
        "keys_required": ["INSTAGRAM_ACCOUNT_ID"],
        "steps": [
            ("Connect Facebook first (same app, same credentials)", ""),
            ("Make sure your Instagram Business account is linked to your Facebook Page", ""),
            ("Click Detect below — we'll find your Instagram account automatically", ""),
        ],
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
        "id": "youtube", "name": "YouTube", "icon": "bi-youtube", "color": "#ff0000",
        "note": "Posts videos as YouTube Shorts to your channel",
        "type": "oauth",
        "keys_required": ["YOUTUBE_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "YOUTUBE_CLIENT_ID",     "label": "Client ID"},
            {"key": "YOUTUBE_CLIENT_SECRET", "label": "Client Secret", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://console.cloud.google.com → APIs & Services → Credentials"),
            ("Create a project, enable YouTube Data API v3", ""),
            ("Create OAuth 2.0 Client ID → choose Desktop app", ""),
            ("Add redirect URI:", "http://localhost:8080/callback"),
            ("Enter credentials below, then click Launch Auth", ""),
        ],
        "auth_script": "bin/auth/youtube.py",
        "verify_cmd": "youtube",
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

_oauth_processes      = {}  # platform_id → subprocess
_oauth_account_counts = {}  # platform_id → account count at time OAuth was launched
_oauth_env_had_tokens = {}  # platform_id → bool: did .env already have tokens when OAuth launched


def _platform_status_env_only(p):
    """Check only .env — used to snapshot state before OAuth launches."""
    load_dotenv(ENV_FILE, override=True)
    return all(os.getenv(k) for k in p["keys_required"])


def _platform_status(p):
    # Account store is the primary source of truth
    if _get_acct_store().get_active(p["id"]):
        return True
    # Fall back to .env only for first-time setup before any account is saved
    return _platform_status_env_only(p)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB — enough for video
app.secret_key = _ensure_secret_key()

# Trust Cloudflare's forwarded headers so Flask generates correct https:// URLs
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

QUEUE_DIR  = ROOT / "famjammemes" / "queue"
POSTED_DIR = ROOT / "famjammemes" / "posted"
_pull_engine = PullEngine(QUEUE_DIR, ENV_FILE)
QUEUE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}
IMAGE_EXTENSIONS = QUEUE_EXTENSIONS  # keep alias used elsewhere


@app.after_request
def no_cache_html(response):
    """Prevent browsers and Cloudflare from caching HTML pages so JS changes are always fresh."""
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


def _list_dir(folder, reverse=False):
    if not folder.exists():
        return []
    files = sorted(
        (f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in QUEUE_EXTENSIONS),
        key=lambda f: f.stat().st_mtime,
        reverse=reverse,
    )
    return [
        {
            "filename": f.name,
            "caption": f.stem.replace("_", " ").replace("-", " "),
            "mtime": int(f.stat().st_mtime * 1000),  # ms epoch for JS
        }
        for f in files
    ]


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/data-deletion")
def data_deletion():
    return render_template("data_deletion.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = _user_store.authenticate(username, password)
        if user:
            session["user"] = user["username"]
            session["role"] = user["role"]
            next_url = request.form.get("next") or "/queue"
            return redirect(next_url)
        error = "Invalid username or password."
    next_url = request.args.get("next", "")
    return render_template("login.html", error=error, next=next_url)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    data    = request.get_json()
    current = data.get("current_password", "")
    new_pw  = data.get("new_password", "")
    if not new_pw or len(new_pw) < 4:
        return jsonify({"error": "New password must be at least 4 characters"}), 400

    username = session["user"]
    if not _user_store.authenticate(username, current):
        return jsonify({"error": "Current password is incorrect"}), 403

    _user_store.update_password(username, new_pw)
    return jsonify({"ok": True})


@app.route("/api/auth/me")
@login_required
def api_auth_me():
    user = _user_store.get(session["user"])
    default_pw = _user_store.is_default_password(session["user"])
    return jsonify({
        "user":             session["user"],
        "role":             session["role"],
        "default_password": default_pw,
    })


# ── User management (admin only) ───────────────────────────────────────────────

@app.route("/users")
@admin_required
def users_page():
    return render_template("users.html")


@app.route("/api/users")
@admin_required
def api_users_list():
    return jsonify(_user_store.list_all())


@app.route("/api/users", methods=["POST"])
@admin_required
def api_users_create():
    data     = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "user")
    if not username or not password or len(password) < 4:
        return jsonify({"error": "Username and password (min 4 chars) required"}), 400
    if role not in UserStore.ROLES:
        return jsonify({"error": f"Invalid role. Must be one of: {', '.join(UserStore.ROLES)}"}), 400
    if _user_store.get(username):
        return jsonify({"error": f"Username '{username}' already exists"}), 409
    user = _user_store.create(username, password, role)
    return jsonify({"ok": True, "user": user})


@app.route("/api/users/<username>", methods=["DELETE"])
@admin_required
def api_users_delete(username):
    if username == session["user"]:
        return jsonify({"error": "Cannot delete your own account"}), 400
    _user_store.delete(username)
    # Clear their cached AccountStore
    _acct_stores.pop(username, None)
    return jsonify({"ok": True})


@app.route("/api/users/<username>/role", methods=["POST"])
@admin_required
def api_users_update_role(username):
    data = request.get_json()
    role = data.get("role", "")
    if role not in UserStore.ROLES:
        return jsonify({"error": "Invalid role"}), 400
    if username == session["user"] and role != "admin":
        return jsonify({"error": "Cannot remove admin role from yourself"}), 400
    _user_store.update_role(username, role)
    return jsonify({"ok": True})


@app.route("/api/users/<username>/reset-password", methods=["POST"])
@admin_required
def api_users_reset_password(username):
    data     = request.get_json()
    new_pw   = data.get("password", "")
    if len(new_pw) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400
    _user_store.update_password(username, new_pw)
    return jsonify({"ok": True})


# ── App routes ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return redirect("/queue")


@app.route("/queue")
@login_required
def queue():
    from pipeline.core.content_store import ContentStore
    try:
        db_stats = ContentStore().stats()
    except Exception:
        db_stats = {"total": 0, "posted": 0, "failed": 0}
    posted_items = _list_dir(POSTED_DIR, reverse=True)
    return render_template(
        "queue.html",
        images=_list_dir(QUEUE_DIR),
        posted=posted_items,
        stats={
            "queue":  len(_list_dir(QUEUE_DIR)),
            "posted": len(posted_items),
            "db_posted": db_stats.get("posted", 0),
        },
    )


@app.route("/media/queue/<path:filename>")
@login_required
def serve_queue_media(filename):
    return send_from_directory(QUEUE_DIR, filename)


@app.route("/media/posted/<path:filename>")
@login_required
def serve_posted_media(filename):
    return send_from_directory(POSTED_DIR, filename)


@app.route("/api/queue/<path:filename>", methods=["DELETE"])
@login_required
def api_queue_delete(filename):
    p = QUEUE_DIR / filename
    if p.exists() and p.is_file():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/posted/<path:filename>", methods=["DELETE"])
@login_required
def api_posted_delete(filename):
    p = POSTED_DIR / filename
    if p.exists() and p.is_file():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/queue/<path:filename>/move-to-posted", methods=["POST"])
@login_required
def api_move_to_posted(filename):
    src = QUEUE_DIR / filename
    if not src.exists():
        return jsonify({"error": "File not found"}), 404
    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    dest = POSTED_DIR / filename
    stem, suffix = src.stem, src.suffix
    counter = 1
    while dest.exists():
        dest = POSTED_DIR / f"{stem}_{counter}{suffix}"
        counter += 1
    src.rename(dest)
    return jsonify({"ok": True})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "File too large (max 500 MB)"}), 413


@app.route("/api/queue/upload", methods=["POST"])
@admin_required
def api_queue_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    uploaded = request.files.getlist("file")
    results = []
    for f in uploaded:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in QUEUE_EXTENSIONS:
            results.append({"ok": False, "filename": f.filename, "error": f"Unsupported type: {ext}"})
            continue
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        dest = QUEUE_DIR / Path(f.filename).name
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = QUEUE_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
        f.save(dest)
        results.append({"ok": True, "filename": dest.name})

    if not results:
        return jsonify({"error": "No valid files received"}), 400

    # Auto-schedule if trigger is set to on_upload
    cfg = _load_default_sched()
    if cfg.get("enabled") and cfg.get("trigger") == "on_upload" and cfg.get("platforms"):
        from datetime import datetime, timedelta
        existing = {p["filename"] for p in _sched_store.list_all(status="pending")}
        days_ahead   = int(cfg.get("days_ahead", 7))
        times        = cfg.get("times", [])
        days_of_week = cfg.get("days_of_week", [])
        platforms    = cfg.get("platforms", [])
        now = datetime.now()
        slots = []
        for day_offset in range(days_ahead + 1):
            day = now + timedelta(days=day_offset)
            if day.weekday() not in days_of_week:
                continue
            for t in sorted(times):
                h, m = map(int, t.split(":"))
                slot_dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
                if slot_dt > now:
                    slots.append(slot_dt)
        occupied = {p["scheduled_at"][:16] for p in _sched_store.list_all(status="pending")}
        free_slots = [s for s in slots if s.strftime("%Y-%m-%dT%H:%M") not in occupied]
        for slot_dt, r in zip(free_slots, [r for r in results if r["ok"]]):
            if r["filename"] not in existing:
                captions = {p: Path(r["filename"]).stem.replace("_", " ") for p in platforms}
                _sched_store.add(r["filename"], captions, platforms, {},
                                 slot_dt.isoformat(timespec="minutes"))

    return jsonify({"ok": True, "results": results})


@app.route("/queue/<path:filename>")
@login_required
def editor(filename):
    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return "Image not found", 404
    caption = image_path.stem.replace("_", " ").replace("-", " ")
    return render_template("editor.html", filename=filename, caption=caption)


@app.route("/api/generate-captions", methods=["POST"])
@login_required
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
@login_required
def api_publish():
    from pipeline.core.content_item import ContentItem
    from pipeline.core.content_store import ContentStore
    from pipeline.brands.famjam.config import create_pipeline

    data             = request.get_json()
    filename         = data.get("filename")
    captions         = data.get("captions", {})
    platforms        = data.get("platforms", [])
    platform_options = data.get("platform_options", {})

    # Reviewer accounts are restricted to TikTok only
    if session.get("role") == "reviewer":
        platforms = [p for p in platforms if p == "tiktok"]

    if not filename or not platforms:
        return jsonify({"error": "filename and platforms required"}), 400

    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return jsonify({"error": "Image not found"}), 404

    # Report platforms that have no connected account before even running the pipeline
    results = {}
    connected_platforms = []
    acct_store = _get_acct_store()
    for p in platforms:
        if acct_store.get_credentials(p):
            connected_platforms.append(p)
        else:
            results[p] = {"status": "skipped", "detail": "No account connected — go to Accounts to connect"}

    if not connected_platforms:
        return jsonify(results)

    store    = ContentStore()
    pipeline = create_pipeline(platforms=connected_platforms, store=store,
                               account_store=acct_store)

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

    for platform_config in pipeline.platforms:
        pname = platform_config.name
        try:
            import io, contextlib
            platform_item = copy.deepcopy(item)
            platform_item.caption = captions.get(pname, item.caption)
            # Inject platform-specific options into metadata
            if pname in platform_options:
                platform_item.metadata.update(platform_options[pname])

            for transformer in platform_config.transformers:
                platform_item = transformer.transform(platform_item)

            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                success = platform_config.publisher.publish(platform_item)
            output = captured.getvalue().strip()

            if success:
                post_id = (platform_item.metadata.get(f"{pname}_post_id")
                           or platform_item.metadata.get(f"{pname}_publish_id"))
                store.mark_posted(item.id, pname, post_id)
                results[pname] = {"status": "posted", "post_id": post_id}
            else:
                store.mark_failed(item.id, pname)
                results[pname] = {"status": "failed", "detail": output or "unknown error"}
        except Exception as e:
            results[pname] = {"status": "error", "detail": str(e)}

    return jsonify(results)


@app.route("/accounts")
@login_required
def accounts_page():
    role = session.get("role", "user")
    acct = _get_acct_store()
    platforms = []
    for p in SETUP_PLATFORMS:
        # Reviewer only sees TikTok
        if role == "reviewer" and p["id"] != "tiktok":
            continue
        active = acct.get_active(p["id"])
        platforms.append({
            **p,
            "configured": bool(acct.get_active(p["id"])) or _platform_status_env_only(p),
            "account_name": active.account_name if active else None,
        })
    return render_template("setup.html", platforms=platforms)


@app.route("/accounts/<platform_id>")
@login_required
def accounts_platform(platform_id):
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


@app.route("/api/accounts/save-keys", methods=["POST"])
@login_required
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


@app.route("/api/accounts/launch-oauth/<platform_id>", methods=["POST"])
@login_required
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
        # Snapshot state so polling can detect genuinely new tokens/accounts
        _oauth_account_counts[platform_id] = len(_get_acct_store().list_all(platform_id))
        _oauth_env_had_tokens[platform_id] = _platform_status_env_only(p)

        def _stream():
            for line in proc.stdout:
                pass  # drain so it doesn't block

        threading.Thread(target=_stream, daemon=True).start()
        return jsonify({"ok": True, "pid": proc.pid, "message": f"Auth flow started in terminal. Check your browser for the OAuth window."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/status/<platform_id>")
@login_required
def api_setup_status(platform_id):
    p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
    if not p:
        return jsonify({"error": "Unknown platform"}), 404
    proc    = _oauth_processes.get(platform_id)
    running = proc is not None and proc.poll() is None

    baseline_count = _oauth_account_counts.get(platform_id)
    if baseline_count is None:
        # No OAuth in progress — simple check
        configured = _platform_status(p)
    else:
        current_count = len(_get_acct_store().list_all(platform_id))
        # Case 1: second+ account — new account appeared in the store
        new_account_saved = current_count > baseline_count
        # Case 2: first account — subprocess finished successfully and tokens are in .env
        proc_succeeded = proc is not None and proc.poll() == 0
        tokens_ready   = proc_succeeded and _platform_status_env_only(p)
        configured = new_account_saved or tokens_ready

    return jsonify({"configured": configured, "oauth_running": running})


@app.route("/api/accounts/verify/<platform_id>", methods=["GET", "POST"])
@login_required
def api_setup_verify(platform_id):
    load_dotenv(ENV_FILE, override=True)
    # Credentials can be passed in POST body (manual platforms / adding a second account)
    # or fall back to current env (after OAuth flow writes to .env)
    body_creds = (request.get_json(silent=True) or {}).get("credentials", {})

    try:
        if platform_id == "instagram" and not body_creds.get("INSTAGRAM_ACCOUNT_ID"):
            # Derive Instagram from existing Facebook credentials
            import requests as _req
            fb_acct = _get_acct_store().get_active("facebook")
            if not fb_acct or not fb_acct.credentials:
                return jsonify({"ok": False, "message": "Connect Facebook first — Instagram uses the same app"})
            fb_creds   = fb_acct.credentials
            page_id    = fb_creds.get("FACEBOOK_PAGE_ID")
            page_token = fb_creds.get("FACEBOOK_PAGE_ACCESS_TOKEN")
            if not page_id or not page_token:
                return jsonify({"ok": False, "message": "Facebook Page credentials missing — reconnect Facebook"})
            resp = _req.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"fields": "instagram_business_account", "access_token": page_token},
                timeout=10,
            )
            if not resp.ok:
                return jsonify({"ok": False, "message": f"Facebook API error: {resp.text}"})
            page_data = resp.json()
            print(f"[instagram] page API response: {page_data}")
            ig_data = page_data.get("instagram_business_account")
            if not ig_data:
                return jsonify({"ok": False,
                    "message": "No Instagram Business account linked to this Facebook Page. "
                               "In Facebook Page settings → Linked Accounts → connect your Instagram."})
            ig_id = ig_data["id"]
            # Verify the IG account is accessible
            ig_resp = _req.get(
                f"https://graph.instagram.com/v21.0/{ig_id}",
                params={"fields": "id,username", "access_token": page_token},
                timeout=10,
            )
            username = ""
            if ig_resp.ok:
                username = ig_resp.json().get("username", "")
            acct = _get_acct_store()
            creds = {
                **fb_creds,
                "INSTAGRAM_ACCOUNT_ID":   ig_id,
                "INSTAGRAM_ACCESS_TOKEN": page_token,
            }
            account = acct.add("instagram", f"@{username}" if username else ig_id, ig_id, creds)
            return jsonify({"ok": True,
                "message": f"Instagram connected{' as @' + username if username else ''}",
                "account": account.public_dict()})

        if platform_id == "reddit":
            import praw
            client_id     = body_creds.get("REDDIT_CLIENT_ID")     or os.environ["REDDIT_CLIENT_ID"]
            client_secret = body_creds.get("REDDIT_CLIENT_SECRET") or os.environ["REDDIT_CLIENT_SECRET"]
            reddit = praw.Reddit(client_id=client_id, client_secret=client_secret,
                                 user_agent="socialline/1.0")
            list(reddit.subreddit("memes").hot(limit=1))
            acct = _get_acct_store()
            creds = body_creds or acct.snapshot_from_env("reddit")
            acct.add("reddit", "Reddit (read-only)", "reddit", creds)
            return jsonify({"ok": True, "message": "Reddit read-only access confirmed"})

        publisher = _make_publisher(platform_id, body_creds or None)
        if publisher is None:
            return jsonify({"error": "Unknown platform"}), 404

        info = publisher.get_account_info()
        if not info:
            return jsonify({"ok": False, "message": "Connection failed — check credentials"})

        acct = _get_acct_store()
        creds = body_creds or acct.snapshot_from_env(platform_id)
        account = acct.add(platform_id, info["name"], info["id"], creds)
        # Reset OAuth baseline so subsequent status checks use simple configured check
        _oauth_account_counts.pop(platform_id, None)
        return jsonify({
            "ok": True,
            "message": f"Connected as {info['name']}",
            "account": account.public_dict(),
        })

    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


def _make_publisher(platform_id: str, credentials: dict = None):
    """Instantiate a publisher for the given platform."""
    mapping = {
        "facebook":  ("pipeline.platforms.facebook.publisher",  "FacebookPublisher"),
        "instagram": ("pipeline.platforms.instagram.publisher", "InstagramPublisher"),
        "threads":   ("pipeline.platforms.threads.publisher",   "ThreadsPublisher"),
        "x":         ("pipeline.platforms.x.publisher",         "XPublisher"),
        "tiktok":    ("pipeline.platforms.tiktok.publisher",    "TikTokPublisher"),
        "bluesky":   ("pipeline.platforms.bluesky.publisher",   "BlueskyPublisher"),
        "linkedin":  ("pipeline.platforms.linkedin.publisher",  "LinkedInPublisher"),
        "pinterest": ("pipeline.platforms.pinterest.publisher", "PinterestPublisher"),
        "youtube":   ("pipeline.platforms.youtube.publisher",   "YouTubePublisher"),
    }
    if platform_id not in mapping:
        return None
    import importlib
    mod = importlib.import_module(mapping[platform_id][0])
    return getattr(mod, mapping[platform_id][1])(credentials)


# ── Account management API ─────────────────────────────────────────────────

@app.route("/api/accounts/import-from-env/<platform_id>", methods=["POST"])
@login_required
def api_import_from_env(platform_id):
    """Auto-import credentials already in .env into the account store."""
    load_dotenv(ENV_FILE, override=True)
    acct = _get_acct_store()
    try:
        if platform_id == "reddit":
            import praw
            reddit = praw.Reddit(
                client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
                client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
                user_agent="socialline/1.0",
            )
            list(reddit.subreddit("memes").hot(limit=1))
            creds = acct.snapshot_from_env("reddit")
            acct.add("reddit", "Reddit (read-only)", "reddit", creds)
            return jsonify({"ok": True, "message": "Reddit imported"})

        publisher = _make_publisher(platform_id)
        if publisher is None:
            return jsonify({"error": "Unknown platform"}), 404

        info = publisher.get_account_info()
        if not info:
            return jsonify({"ok": False, "message": "Could not verify — check .env credentials"})

        creds   = acct.snapshot_from_env(platform_id)
        account = acct.add(platform_id, info["name"], info["id"], creds)
        return jsonify({"ok": True, "message": f"Imported {info['name']}", "account": account.public_dict()})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


def _tiktok_publisher():
    """Create a TikTokPublisher with a refresh callback that persists new tokens."""
    from pipeline.platforms.tiktok.publisher import TikTokPublisher
    acct  = _get_acct_store()
    creds = acct.get_credentials("tiktok")

    def _on_refresh(new_access_token, new_refresh_token):
        account = acct.get_active("tiktok")
        if account:
            updated = {**account.credentials,
                       "TIKTOK_ACCESS_TOKEN":  new_access_token,
                       "TIKTOK_REFRESH_TOKEN": new_refresh_token}
            acct.add("tiktok", account.account_name, account.account_id, updated)

    return TikTokPublisher(creds or None, on_token_refresh=_on_refresh)


@app.route("/api/tiktok/creator-info")
@login_required
def api_tiktok_creator_info():
    try:
        info = _tiktok_publisher().get_creator_info()
        if info:
            return jsonify({"ok": True, "data": info})
        return jsonify({"ok": False, "message": "Could not fetch creator info — check TikTok credentials"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/tiktok/status/<publish_id>")
@login_required
def api_tiktok_status(publish_id):
    try:
        status = _tiktok_publisher().poll_status(publish_id)
        return jsonify({"status": status})
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)})


@app.route("/api/accounts")
@login_required
def api_accounts_all():
    accounts = _get_acct_store().list_all()
    return jsonify([a.public_dict() for a in accounts])


@app.route("/api/accounts/<platform_id>")
@login_required
def api_accounts_for_platform(platform_id):
    accounts = _get_acct_store().list_all(platform_id)
    return jsonify([a.public_dict() for a in accounts])


@app.route("/api/accounts/<account_id>/activate", methods=["POST"])
@login_required
def api_account_activate(account_id):
    ok = _get_acct_store().set_active(account_id)
    return jsonify({"ok": ok})


# User-level token keys that should be cleared from .env on logout
# (excludes app/developer credentials like CLIENT_KEY, CLIENT_SECRET, APP_ID etc.)
_USER_TOKEN_KEYS: dict[str, list[str]] = {
    "facebook":  ["FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"],
    "instagram": ["INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN"],
    "threads":   ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN"],
    "x":         ["X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
                  "X_CONSUMER_KEY", "X_CONSUMER_SECRET"],
    "tiktok":    ["TIKTOK_ACCESS_TOKEN", "TIKTOK_OPEN_ID", "TIKTOK_REFRESH_TOKEN"],
    "bluesky":   ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "linkedin":  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"],
    "pinterest": ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID"],
    "youtube":   ["YOUTUBE_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN"],
    "reddit":    ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
}


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
@login_required
def api_account_delete(account_id):
    acct = _get_acct_store()
    account = next((a for a in acct.list_all() if a.id == account_id), None)
    ok = acct.remove(account_id)

    # If no more accounts remain for this platform, clear user tokens from .env
    if ok and account:
        remaining = acct.list_all(account.platform)
        if not remaining:
            keys_to_clear = _USER_TOKEN_KEYS.get(account.platform, [])
            if keys_to_clear and ENV_FILE.exists():
                for k in keys_to_clear:
                    set_key(str(ENV_FILE), k, "")
                load_dotenv(ENV_FILE, override=True)

    return jsonify({"ok": ok})


@app.route("/api/restart", methods=["POST"])
@admin_required
def api_restart():
    def _restart():
        import time
        time.sleep(0.4)
        subprocess.Popen([sys.executable, "app/server.py"], cwd=str(ROOT))
        os._exit(0)
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"ok": True})


# ── Sources ────────────────────────────────────────────────────────────────────

@app.route("/sources")
@login_required
def sources_page():
    return render_template("sources.html")


@app.route("/api/sources", methods=["GET"])
@login_required
def api_sources_list():
    return jsonify(_source_store.list_all())


@app.route("/api/sources", methods=["POST"])
@not_reviewer
def api_sources_create():
    data     = request.get_json()
    name     = data.get("name", "").strip()
    type_    = data.get("type", "")
    config   = data.get("config", {})
    schedule = data.get("schedule")
    if not name or not type_:
        return jsonify({"error": "name and type required"}), 400
    source = _source_store.add(name, type_, config, schedule)
    return jsonify({"ok": True, "source": source})


@app.route("/api/sources/<source_id>", methods=["PUT"])
@not_reviewer
def api_sources_update(source_id):
    data = request.get_json()
    fields = {}
    for k in ("name", "config", "enabled", "schedule"):
        if k in data:
            fields[k] = data[k]
    _source_store.update(source_id, **fields)
    return jsonify({"ok": True, "source": _source_store.get(source_id)})


@app.route("/api/sources/<source_id>", methods=["DELETE"])
@not_reviewer
def api_sources_delete(source_id):
    _source_store.delete(source_id)
    return jsonify({"ok": True})


@app.route("/api/sources/<source_id>/pull", methods=["POST"])
@not_reviewer
def api_sources_pull(source_id):
    source = _source_store.get(source_id)
    if not source:
        return jsonify({"error": "Source not found"}), 404
    try:
        added = _pull_engine.pull(source)
        _source_store.log_pull(source_id, len(added))
        return jsonify({"ok": True, "added": len(added), "files": added})
    except Exception as e:
        _source_store.log_pull(source_id, 0, status="error", error=str(e))
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sources/<source_id>/log")
@login_required
def api_sources_log(source_id):
    return jsonify(_source_store.recent_log(source_id))


@app.route("/calendar")
@login_required
def calendar_page():
    return render_template("calendar.html")


@app.route("/api/schedule", methods=["POST"])
@login_required
def api_schedule_create():
    data = request.get_json()
    filename         = data.get("filename")
    captions         = data.get("captions", {})
    platforms        = data.get("platforms", [])
    platform_options = data.get("platform_options", {})
    scheduled_at     = data.get("scheduled_at", "")

    if not filename or not platforms or not scheduled_at:
        return jsonify({"error": "filename, platforms and scheduled_at required"}), 400

    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return jsonify({"error": "File not found"}), 404

    # Validate the datetime
    try:
        from datetime import datetime as dt
        scheduled_dt = dt.fromisoformat(scheduled_at)
        if scheduled_dt <= dt.now():
            return jsonify({"error": "Scheduled time must be in the future"}), 400
    except ValueError:
        return jsonify({"error": "Invalid date/time format"}), 400

    post_id = _sched_store.add(filename, captions, platforms, platform_options, scheduled_at)
    return jsonify({"ok": True, "id": post_id})


@app.route("/api/schedule", methods=["GET"])
@login_required
def api_schedule_list():
    posts = _sched_store.list_all()
    return jsonify(posts)


@app.route("/api/schedule/<post_id>", methods=["DELETE"])
@login_required
def api_schedule_delete(post_id):
    _sched_store.delete(post_id)
    return jsonify({"ok": True})


# ── Default schedule config ────────────────────────────────────────────────

_DEFAULT_SCHED_FILE = DATA_DIR / "default_schedule.json"
_DEFAULT_SCHED = {
    "enabled": False,
    "days_of_week": [0, 1, 2, 3, 4],   # 0=Mon … 6=Sun
    "times": ["09:00", "18:00"],
    "days_ahead": 7,
    "platforms": [],
    "trigger": "manual",                # "manual" | "on_upload"
}


def _load_default_sched() -> dict:
    if _DEFAULT_SCHED_FILE.exists():
        try:
            return {**_DEFAULT_SCHED, **json.loads(_DEFAULT_SCHED_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULT_SCHED)


def _save_default_sched(cfg: dict):
    _DEFAULT_SCHED_FILE.write_text(json.dumps(cfg, indent=2))


@app.route("/api/schedule/default", methods=["GET"])
@login_required
def api_schedule_default_get():
    return jsonify(_load_default_sched())


@app.route("/api/schedule/default", methods=["POST"])
@login_required
def api_schedule_default_save():
    cfg = request.get_json()
    allowed = {"enabled", "days_of_week", "times", "days_ahead", "platforms", "trigger"}
    clean = {k: cfg[k] for k in allowed if k in cfg}
    merged = {**_load_default_sched(), **clean}
    _save_default_sched(merged)
    return jsonify({"ok": True})


@app.route("/api/schedule/generate", methods=["POST"])
@login_required
def api_schedule_generate():
    """Fill upcoming schedule slots with queue items using the default config."""
    import json as _json
    from datetime import datetime, timedelta

    cfg      = _load_default_sched()
    days_ahead  = int(cfg.get("days_ahead", 7))
    times       = cfg.get("times", [])
    days_of_week = cfg.get("days_of_week", [])
    platforms   = cfg.get("platforms", [])

    if not times or not platforms:
        return jsonify({"error": "Configure times and platforms first"}), 400

    # Collect already-scheduled filenames so we don't double-schedule
    existing = {p["filename"] for p in _sched_store.list_all(status="pending")}

    # Queue items not yet scheduled, oldest first
    queue_items = [
        f.name for f in sorted(QUEUE_DIR.iterdir(), key=lambda x: x.stat().st_mtime)
        if f.is_file() and f.suffix.lower() in QUEUE_EXTENSIONS
        and f.name not in existing
    ]

    if not queue_items:
        return jsonify({"ok": True, "scheduled": 0, "message": "No unscheduled items in queue"})

    # Build candidate slots: next `days_ahead` days × configured times
    now   = datetime.now()
    slots = []
    for day_offset in range(days_ahead + 1):
        day = now + timedelta(days=day_offset)
        if day.weekday() not in days_of_week:
            continue
        for t in sorted(times):
            h, m = map(int, t.split(":"))
            slot_dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_dt > now:
                slots.append(slot_dt)

    # Check which slots already have a post
    pending = _sched_store.list_all(status="pending")
    occupied = {p["scheduled_at"][:16] for p in pending}   # "YYYY-MM-DDTHH:MM"
    free_slots = [s for s in slots if s.strftime("%Y-%m-%dT%H:%M") not in occupied]

    # Pair free slots with queue items
    count = 0
    for slot_dt, filename in zip(free_slots, queue_items):
        captions = {p: Path(filename).stem.replace("_", " ") for p in platforms}
        _sched_store.add(
            filename=filename,
            captions=captions,
            platforms=platforms,
            platform_options={},
            scheduled_at=slot_dt.isoformat(timespec="minutes"),
        )
        count += 1

    return jsonify({"ok": True, "scheduled": count,
                    "message": f"{count} post{'s' if count != 1 else ''} scheduled"})


if __name__ == "__main__":
    import webbrowser, threading
    print()
    print("  FamJam Socialline")
    print("  https://socialline.space  (public)")
    print("  http://localhost:5000     (local)")
    print(f"  Queue: {QUEUE_DIR}")
    print()
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "famjammemes").mkdir(parents=True, exist_ok=True)
    start_scheduler(_sched_store, QUEUE_DIR)
    threading.Timer(1.0, lambda: webbrowser.open("https://socialline.space")).start()
    app.run(debug=False, port=5000)
