#!/usr/bin/env python3
import sys
import copy
import json
import logging
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

from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, session, redirect, abort
from urllib.parse import urlencode
from pipeline.core.accounts import AccountStore
from users import UserStore
from scheduler import ScheduleStore, start_scheduler, start_source_puller

ENV_FILE   = ROOT / ".env"
DATA_DIR   = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Error log ─────────────────────────────────────────────────────────────────
_log_file = DATA_DIR / "errors.log"
logging.basicConfig(
    filename=str(_log_file),
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_elog = logging.getLogger("socialline")

# ── Brand store ───────────────────────────────────────────────────────────────
import brands as _brands_module
_brands_module.BRAND_DATA_ROOT = DATA_DIR / "brands"
_brands_module.BRAND_DATA_ROOT.mkdir(parents=True, exist_ok=True)
from brands import BrandStore
_brand_store = BrandStore(DATA_DIR / "brands.db")

_user_store = UserStore(DATA_DIR / "users.db")

from sources import SourceStore, PullEngine

# ── Per-brand resource caches (keyed by brand_id) ────────────────────────────
_brand_sched_stores:  dict[str, ScheduleStore] = {}
_brand_source_stores: dict[str, SourceStore]   = {}
_brand_pull_engines:  dict[str, "PullEngine"]  = {}
_brand_acct_stores:   dict[str, AccountStore]  = {}
_brand_render_stores: dict = {}
_detected_encoder:    str  = None   # cached after first probe



# ── Per-brand path/resource helpers ──────────────────────────────────────────

def _get_active_brand() -> "dict | None":
    try:
        from flask import session as _sess
        bid = _sess.get("active_brand_id")
    except RuntimeError:
        return None
    return _brand_store.get(bid) if bid else None


def _brand_dir_for(brand_id: str) -> Path:
    return DATA_DIR / "brands" / brand_id


def _get_queue_dir() -> Path:
    b = _get_active_brand()
    if not b:
        abort(409, "No active brand selected")
    return _brand_dir_for(b["id"]) / "queue"


def _get_posted_dir() -> Path:
    b = _get_active_brand()
    if not b:
        abort(409, "No active brand selected")
    return _brand_dir_for(b["id"]) / "posted"


def _sched_store_for(brand_id: str) -> ScheduleStore:
    if brand_id not in _brand_sched_stores:
        bdir = _brand_dir_for(brand_id)
        bdir.mkdir(parents=True, exist_ok=True)
        _brand_sched_stores[brand_id] = ScheduleStore(bdir / "schedule.db")
    return _brand_sched_stores[brand_id]


def _get_sched_store() -> ScheduleStore:
    b = _get_active_brand()
    if b:
        return _sched_store_for(b["id"])
    abort(409, "No active brand selected")


def _source_store_for(brand_id: str) -> SourceStore:
    if brand_id not in _brand_source_stores:
        bdir = _brand_dir_for(brand_id)
        bdir.mkdir(parents=True, exist_ok=True)
        _brand_source_stores[brand_id] = SourceStore(bdir / "sources.db")
    return _brand_source_stores[brand_id]


def _get_source_store() -> SourceStore:
    b = _get_active_brand()
    if b:
        return _source_store_for(b["id"])
    abort(409, "No active brand selected")


def _pull_engine_for(brand_id: str) -> PullEngine:
    if brand_id not in _brand_pull_engines:
        queue_dir = _brand_dir_for(brand_id) / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        _brand_pull_engines[brand_id] = PullEngine(queue_dir, ENV_FILE, brand_id)
    return _brand_pull_engines[brand_id]


def _get_pull_engine() -> PullEngine:
    b = _get_active_brand()
    if b:
        return _pull_engine_for(b["id"])
    abort(409, "No active brand selected")


def _render_store_for(brand_id: str):
    if brand_id not in _brand_render_stores:
        from render_store import RenderStore
        _brand_render_stores[brand_id] = RenderStore(brand_id, DATA_DIR / "brands")
    return _brand_render_stores[brand_id]


def _get_render_store():
    b = _get_active_brand()
    if b:
        return _render_store_for(b["id"])
    abort(409, "No active brand selected")


def _load_brand_video_config(brand_id: str) -> dict:
    """Load video_pipeline section from data/brands/<bid>/config.json."""
    cfg_file = _brand_dir_for(brand_id) / "config.json"
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text())
        except Exception:
            pass
    return {}


def _acct_store_for(brand_id: str) -> AccountStore:
    if brand_id not in _brand_acct_stores:
        accts_file = _brand_dir_for(brand_id) / "accounts.enc"
        _brand_acct_stores[brand_id] = AccountStore(accts_file)
    return _brand_acct_stores[brand_id]


def _get_acct_store(username: str = None) -> AccountStore:
    b = _get_active_brand()
    if b:
        return _acct_store_for(b["id"])
    abort(409, "No active brand selected")


def _get_content_store():
    from pipeline.core.content_store import ContentStore
    b = _get_active_brand()
    if not b:
        abort(409, "No active brand selected")
    return ContentStore(_brand_dir_for(b["id"]) / "content.db")


# ── Multi-brand scheduler/puller helpers ─────────────────────────────────────

def _all_brand_scheduler_data():
    """Returns list of (sched_store, queue_dir, accounts_file) for all brands."""
    result = []
    for brand in _brand_store.list_all():
        bid = brand["id"]
        bdir = _brand_dir_for(bid)
        result.append((
            _sched_store_for(bid),
            bdir / "queue",
            bdir / "accounts.enc",
        ))
    return result


def _all_brand_puller_data():
    """Returns (source_store, pull_engine, sched_store, queue_dir, sched_cfg) per brand."""
    result = []
    for brand in _brand_store.list_all():
        bid = brand["id"]
        bdir = _brand_dir_for(bid)
        result.append((
            _source_store_for(bid),
            _pull_engine_for(bid),
            _sched_store_for(bid),
            bdir / "queue",
            bdir / "default_schedule.json",
        ))
    return result


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
        "auth_script": "bin/auth/instagram_direct.py",
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
        "id": "telegram", "name": "Telegram", "icon": "bi-telegram", "color": "#229ed9",
        "note": "Posts to a Telegram channel via Bot API — no app review needed",
        "type": "manual",
        "keys_required": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "prereq_fields": [
            {"key": "TELEGRAM_BOT_TOKEN", "label": "Bot Token", "type": "password",
             "help": "Create via @BotFather → /newbot → copy the token"},
        ],
        "form_fields": [
            {"key": "TELEGRAM_CHAT_ID", "label": "Channel ID",
             "help": "Forward a message from your channel to @getidsbot — copy the numeric ID (starts with -100)"},
        ],
        "steps": [
            ("Open Telegram, message", "@BotFather"),
            ("Send /newbot, follow the steps, copy the token", ""),
            ("Add the bot as admin to your channel (Post Messages permission)", ""),
            ("Get channel ID: forward a channel message to", "@getidsbot"),
        ],
        "auth_script": None,
        "verify_cmd": "telegram",
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
        "auth_script": None,
        "verify_cmd": "youtube",
    },
    {
        "id": "x", "name": "X (Twitter)", "icon": "bi-twitter-x", "color": "#000000",
        "note": "Requires X Developer account — enter Consumer Key/Secret, then authorize",
        "type": "oauth",
        "keys_required": ["X_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "X_CONSUMER_KEY",    "label": "Consumer Key (API Key)"},
            {"key": "X_CONSUMER_SECRET", "label": "Consumer Secret (API Key Secret)", "type": "password"},
        ],
        "steps": [
            ("Go to", "https://developer.twitter.com → Projects & Apps → your app"),
            ("Under User authentication settings set permissions to Read and Write", ""),
            ("Set callback URL to", "https://app.socialline.space/callback"),
            ("Copy Consumer Key and Secret from Keys and Tokens", ""),
            ("Enter them below, then click Authorize with X", ""),
        ],
        "auth_script": None,
        "verify_cmd": "x",
    },

    # ── Content Platforms ────────────────────────────────────────────────────
    {
        "id": "substack", "name": "Substack", "icon": "bi-envelope-open-heart", "color": "#ff6719",
        "note": "Publish newsletter posts with cover images",
        "section": "social",
        "type": "manual",
        "keys_required": ["SUBSTACK_SESSION_TOKEN", "SUBSTACK_PUBLICATION"],
        "form_fields": [
            {"key": "SUBSTACK_PUBLICATION", "label": "Publication name",
             "placeholder": "cognifylearn",
             "help": "Your Substack handle — the part after @ in substack.com/@yourhandle. You can also paste the full URL."},
            {"key": "SUBSTACK_SESSION_TOKEN", "label": "Session cookie", "type": "password",
             "help": "Chrome/Edge: DevTools (F12) → Application → Cookies → substack.com → substack.sid. Firefox: DevTools (F12) → Storage → Cookies → substack.com → substack.sid"},
        ],
        "steps": [
            ("Log into substack.com in your browser", ""),
            ("Open DevTools: press F12 (or right-click anywhere → Inspect)", ""),
            ("Chrome / Edge: click the 'Application' tab → expand 'Cookies' → click 'https://substack.com'", ""),
            ("Firefox: click the 'Storage' tab → expand 'Cookies' → click 'https://substack.com'", ""),
            ("Find the cookie named 'substack.sid' and copy its full Value", ""),
        ],
        "auth_script": None,
        "verify_cmd": "substack",
    },

    # ── Selling Platforms ────────────────────────────────────────────────────
    {
        "id": "etsy", "name": "Etsy", "icon": "bi-shop", "color": "#f56400",
        "note": "Create product listings with images (draft mode)",
        "section": "selling",
        "type": "oauth",
        "keys_required": ["ETSY_ACCESS_TOKEN"],
        "prereq_fields": [
            {"key": "ETSY_API_KEY",   "label": "API Key (Keystring)",
             "help": "From your Etsy app at etsy.com/developers"},
            {"key": "ETSY_SHOP_ID",   "label": "Shop ID",
             "help": "Numeric ID from your Etsy shop URL (etsy.com/shop/YourShop — check your browser for the ID)"},
            {"key": "ETSY_DEFAULT_PRICE",       "label": "Default listing price (USD)", "placeholder": "9.99"},
            {"key": "ETSY_DEFAULT_TAXONOMY_ID", "label": "Category ID", "placeholder": "2078",
             "help": "Category taxonomy ID — 2078 = Photography. Browse categories at openapi.etsy.com/v3/application/seller-taxonomy/nodes"},
        ],
        "steps": [
            ("Go to", "https://www.etsy.com/developers/register → Register an app"),
            ("Set callback URL to", "https://app.socialline.space/callback"),
            ("Enter your API Key, Shop ID, and defaults above, then click Connect", ""),
            ("Listings are created as drafts — review and publish from your Etsy dashboard", ""),
        ],
        "auth_script": None,
        "verify_cmd": "etsy",
    },
    {
        "id": "lemonsqueezy", "name": "Lemon Squeezy", "icon": "bi-currency-dollar", "color": "#ffd000",
        "note": "View products and store — read-only connection",
        "section": "selling",
        "type": "manual",
        "keys_required": ["LEMONSQUEEZY_API_KEY"],
        "form_fields": [
            {"key": "LEMONSQUEEZY_API_KEY", "label": "API Key", "type": "password",
             "help": "Generate at app.lemonsqueezy.com → Settings → API"},
        ],
        "steps": [
            ("Go to", "https://app.lemonsqueezy.com/settings/api"),
            ("Click 'Add API key', name it and copy the value", ""),
            ("Paste it below — product creation must be done in the dashboard", ""),
        ],
        "auth_script": None,
        "verify_cmd": "lemonsqueezy",
    },
    {
        "id": "gumroad", "name": "Gumroad", "icon": "bi-bag-heart", "color": "#ff90e8",
        "note": "View products and sales — read-only connection",
        "section": "selling",
        "type": "manual",
        "keys_required": ["GUMROAD_ACCESS_TOKEN"],
        "form_fields": [
            {"key": "GUMROAD_ACCESS_TOKEN", "label": "Access token", "type": "password",
             "help": "Go to app.gumroad.com/api → generate an access token"},
        ],
        "steps": [
            ("Go to", "https://app.gumroad.com/api"),
            ("Click 'Generate access token' and copy it", ""),
            ("Paste it below", ""),
        ],
        "auth_script": None,
        "verify_cmd": "gumroad",
    },
    {
        "id": "teachable", "name": "Teachable", "icon": "bi-mortarboard", "color": "#00b3a4",
        "note": "View courses — read-only connection (Pro plan required)",
        "section": "selling",
        "type": "manual",
        "keys_required": ["TEACHABLE_API_KEY"],
        "form_fields": [
            {"key": "TEACHABLE_API_KEY", "label": "API Key", "type": "password",
             "help": "Go to your school admin → Settings → Integrations → API"},
        ],
        "steps": [
            ("Go to your Teachable school admin → Settings → Integrations", ""),
            ("Copy your API key", ""),
            ("Paste it below (requires Pro plan or higher)", ""),
        ],
        "auth_script": None,
        "verify_cmd": "teachable",
    },
    {
        "id": "udemy", "name": "Udemy", "icon": "bi-play-circle", "color": "#a435f0",
        "note": "View courses and revenue — read-only connection",
        "section": "selling",
        "type": "manual",
        "keys_required": ["UDEMY_BEARER_TOKEN"],
        "form_fields": [
            {"key": "UDEMY_BEARER_TOKEN", "label": "Bearer token", "type": "password",
             "help": "Go to udemy.com → Profile → API Clients → create a client → copy the Bearer Token"},
        ],
        "steps": [
            ("Go to", "https://www.udemy.com/user/edit-profile/"),
            ("Scroll to API Clients → Create a new client", ""),
            ("Copy the Bearer Token value", ""),
            ("Paste it below", ""),
        ],
        "auth_script": None,
        "verify_cmd": "udemy",
    },

    # ── Browser Automation Platforms ────────────────────────────────────────
    {
        "id": "medium", "name": "Medium", "icon": "bi-medium", "color": "#000000",
        "note": "Browser-assisted — fills draft, you click Publish. 3 posts/week.",
        "section": "browser",
        "type": "browser",
        "keys_required": [],
    },
    {
        "id": "quora", "name": "Quora", "icon": "bi-question-circle", "color": "#b92b27",
        "note": "Browser-assisted — answers questions. 1 post/day. Provide question URL.",
        "section": "browser",
        "type": "browser",
        "keys_required": [],
        "form_fields": [
            {"key": "QUORA_QUESTION_URL", "label": "Default question URL",
             "help": "Optional — the Quora question URL to answer by default"},
        ],
    },
    {
        "id": "tpt", "name": "Teachers Pay Teachers", "icon": "bi-book", "color": "#ee4266",
        "note": "Browser-assisted — fills product form, you complete and submit. 5 posts/week.",
        "section": "browser",
        "type": "browser",
        "keys_required": [],
    },
    {
        "id": "substack_notes", "name": "Substack Notes", "icon": "bi-sticky", "color": "#ff6719",
        "note": "Browser-assisted — short notes on Substack's social feed. 3 posts/day.",
        "section": "browser",
        "type": "browser",
        "keys_required": [],
    },
]

_oauth_processes      = {}  # platform_id → subprocess
_oauth_account_counts = {}  # platform_id → account count at time OAuth was launched
_oauth_env_had_tokens = {}  # platform_id → bool: did .env already have tokens when OAuth launched
_web_oauth            = {}  # platform_id → {state, done, code, error} for Flask-native flows


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


@app.context_processor
def _inject_brand_context():
    if not session.get("user"):
        return {"active_brand": None, "user_brands": []}
    active = _get_active_brand()
    username = session["user"]
    all_brands = _brand_store.list_for_user(username)
    return {"active_brand": active, "user_brands": all_brands}

QUEUE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}
IMAGE_EXTENSIONS = QUEUE_EXTENSIONS  # keep alias used elsewhere


_BRAND_FREE_PATHS = {"/login", "/logout", "/api/brands", "/users", "/api/users", "/api/auth"}

@app.before_request
def ensure_active_brand():
    """Auto-assign active_brand_id; redirect brand-less users away from content pages."""
    if not session.get("user"):
        return
    if session.get("active_brand_id"):
        return
    username = session["user"]
    brands = _brand_store.list_for_user(username)
    if brands:
        session["active_brand_id"] = brands[0]["id"]
        return
    # User has no brands — allow only safe paths, redirect everything else
    path = request.path
    if any(path.startswith(p) for p in _BRAND_FREE_PATHS) or path == "/":
        return
    if request.path.startswith("/api/"):
        abort(409, "No active brand — create or join a brand first")
    return redirect("/")


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
            # Set active brand to user's first brand (admin sees all)
            brands = _brand_store.list_for_user(user["username"])
            if brands:
                session["active_brand_id"] = brands[0]["id"]
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
    if not new_pw or len(new_pw) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400

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
    if not username or not password or len(password) < 8:
        return jsonify({"error": "Username and password (min 8 chars) required"}), 400
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
    if len(new_pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    _user_store.update_password(username, new_pw)
    return jsonify({"ok": True})


# ── Brand management ──────────────────────────────────────────────────────────

@app.route("/api/brands")
@login_required
def api_brands_list():
    username  = session["user"]
    active_id = session.get("active_brand_id")
    if session.get("role") == "admin":
        brands = _brand_store.list_all()
    else:
        brands = _brand_store.list_for_user(username)
    return jsonify([{**b, "active": b["id"] == active_id} for b in brands])


@app.route("/api/brands", methods=["POST"])
@login_required
def api_brands_create():
    data  = request.get_json()
    slug  = data.get("slug", "").strip().lower().replace(" ", "-")
    name  = data.get("name", "").strip()
    if not slug or not name:
        return jsonify({"error": "slug and name required"}), 400
    username = session["user"]
    owner = data.get("owner", username) if session.get("role") == "admin" else username
    try:
        brand = _brand_store.create(slug, name, owner)
        return jsonify({"ok": True, "brand": brand})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/brands/<brand_id>/switch", methods=["POST"])
@login_required
def api_brands_switch(brand_id):
    brand = _brand_store.get(brand_id)
    if not brand:
        return jsonify({"error": "Brand not found"}), 404
    if session.get("role") != "admin" and brand["owner_username"] != session["user"]:
        return jsonify({"error": "Access denied"}), 403
    session["active_brand_id"] = brand_id
    return jsonify({"ok": True, "brand": brand})


@app.route("/api/brands/<brand_id>", methods=["DELETE"])
@admin_required
def api_brands_delete(brand_id):
    brand = _brand_store.get(brand_id)
    if not brand:
        return jsonify({"error": "Brand not found"}), 404
    _brand_store.delete(brand_id)
    if session.get("active_brand_id") == brand_id:
        session.pop("active_brand_id", None)
    return jsonify({"ok": True})


@app.route("/api/brands/<brand_id>", methods=["PUT"])
@login_required
def api_brands_update(brand_id):
    brand = _brand_store.get(brand_id)
    if not brand:
        return jsonify({"error": "Brand not found"}), 404
    if brand["owner_username"] != session["user"]:
        return jsonify({"error": "Access denied"}), 403
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    updated = _brand_store.update(brand_id, name)
    return jsonify({"ok": True, "brand": updated})


# ── App routes ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    if not session.get("active_brand_id"):
        return redirect("/users")
    return redirect("/queue")



@app.route("/queue")
@login_required
def queue():
    posted_items = _list_dir(_get_posted_dir(), reverse=True)
    try:
        sched_stats = _get_sched_store().stats()
        all_time_posted = sched_stats.get("published", 0)
    except Exception:
        all_time_posted = 0
    return render_template(
        "queue.html",
        images=_list_dir(_get_queue_dir()),
        posted=posted_items,
        stats={
            "queue":  len(_list_dir(_get_queue_dir())),
            "posted": len(posted_items),
            "db_posted": all_time_posted,
        },
    )


@app.route("/media/queue/<path:filename>")
@login_required
def serve_queue_media(filename):
    return send_from_directory(_get_queue_dir(), filename)


@app.route("/media/posted/<path:filename>")
@login_required
def serve_posted_media(filename):
    return send_from_directory(_get_posted_dir(), filename)


@app.route("/api/queue/<path:filename>", methods=["DELETE"])
@login_required
def api_queue_delete(filename):
    p = _get_queue_dir() / filename
    if p.exists() and p.is_file():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/posted/<path:filename>", methods=["DELETE"])
@login_required
def api_posted_delete(filename):
    p = _get_posted_dir() / filename
    if p.exists() and p.is_file():
        p.unlink()
    return jsonify({"ok": True})


@app.route("/api/posted/clear-all", methods=["POST"])
@login_required
def api_posted_clear_all():
    """Delete every file in the brand's posted directory."""
    posted_dir = _get_posted_dir()
    count = 0
    if posted_dir.exists():
        for f in posted_dir.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
    return jsonify({"ok": True, "deleted": count})


@app.route("/api/queue/<path:filename>/move-to-posted", methods=["POST"])
@login_required
def api_move_to_posted(filename):
    src = _get_queue_dir() / filename
    if not src.exists():
        return jsonify({"error": "File not found"}), 404
    _get_posted_dir().mkdir(parents=True, exist_ok=True)
    dest = _get_posted_dir() / filename
    stem, suffix = src.stem, src.suffix
    counter = 1
    while dest.exists():
        dest = _get_posted_dir() / f"{stem}_{counter}{suffix}"
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
        _get_queue_dir().mkdir(parents=True, exist_ok=True)
        dest = _get_queue_dir() / Path(f.filename).name
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = _get_queue_dir() / f"{stem}_{counter}{suffix}"
            counter += 1
        f.save(dest)
        results.append({"ok": True, "filename": dest.name})

    if not results:
        return jsonify({"error": "No valid files received"}), 400

    # Auto-schedule if trigger is set to on_upload
    cfg = _load_default_sched()
    if cfg.get("enabled") and cfg.get("trigger") == "on_upload" and cfg.get("platforms"):
        from datetime import datetime, timedelta
        existing = {p["filename"] for p in _get_sched_store().list_all(status="pending")}
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
        occupied = {p["scheduled_at"][:16] for p in _get_sched_store().list_all(status="pending")}
        free_slots = [s for s in slots if s.strftime("%Y-%m-%dT%H:%M") not in occupied]
        for slot_dt, r in zip(free_slots, [r for r in results if r["ok"]]):
            if r["filename"] not in existing:
                captions = {p: Path(r["filename"]).stem.replace("_", " ") for p in platforms}
                _get_sched_store().add(r["filename"], captions, platforms, {},
                                 slot_dt.isoformat(timespec="minutes"))

    return jsonify({"ok": True, "results": results})


@app.route("/queue/<path:filename>")
@login_required
def editor(filename):
    image_path = _get_queue_dir() / filename
    if not image_path.exists():
        return "Image not found", 404

    # Load pre-generated caption sidecar if present (e.g. from article source)
    caption_sidecar = _get_queue_dir() / (image_path.stem + ".caption.txt")
    if caption_sidecar.exists():
        caption = caption_sidecar.read_text(encoding="utf-8").strip()
    else:
        caption = image_path.stem.replace("_", " ").replace("-", " ")

    # Check if this came from an article source
    article_sidecar = _get_queue_dir() / (image_path.stem + ".article.json")
    is_article = article_sidecar.exists()

    return render_template("editor.html", filename=filename, caption=caption,
                           is_article=is_article)


@app.route("/api/generate-captions", methods=["POST"])
@login_required
def api_generate_captions():
    from caption_ai import generate_platform_captions, generate_article_captions
    data         = request.get_json() or {}
    core_caption = data.get("caption", "").strip()
    filename     = data.get("filename", "").strip()
    if not core_caption:
        return jsonify({"error": "No caption provided"}), 400

    # Detect article content — check for sidecar or explicit flag
    is_article = False
    article_meta = {}
    if filename:
        article_sidecar = _get_queue_dir() / (Path(filename).stem + ".article.json")
        caption_sidecar = _get_queue_dir() / (Path(filename).stem + ".caption.txt")
        if article_sidecar.exists():
            is_article = True
            article_meta = json.loads(article_sidecar.read_text(encoding="utf-8"))
        elif caption_sidecar.exists():
            is_article = True  # came from article source even if no .article.json

    try:
        if is_article:
            captions = generate_article_captions(core_caption, article_meta)
        else:
            image_path = (_get_queue_dir() / filename) if filename else None
            captions = generate_platform_captions(core_caption, image_path=image_path)
        return jsonify(captions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/publish", methods=["POST"])
@login_required
def api_publish():
    from pipeline.core.content_item import ContentItem
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

    image_path = _get_queue_dir() / filename
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

    store    = _get_content_store()
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
                detail = output or "unknown error"
                store.mark_failed(item.id, pname)
                results[pname] = {"status": "failed", "detail": detail}
                _elog.error("[%s] %s — %s", pname, filename, detail)
        except Exception as e:
            results[pname] = {"status": "error", "detail": str(e)}
            _elog.error("[%s] %s — %s", pname, filename, e)

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
            "section":      p.get("section", "social"),
            "configured":   bool(acct.get_active(p["id"])) or _platform_status_env_only(p),
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
    is_admin = session.get("role") == "admin"
    return render_template("setup_platform.html", p=p, configured=configured, current_values=current_values, is_admin=is_admin)


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


@app.route("/callback")
def oauth_web_callback():
    """Flask-native OAuth callback — handles OAuth 1.0a (X) and OAuth 2.0 (Threads etc.)."""
    oauth_token    = request.args.get("oauth_token")
    oauth_verifier = request.args.get("oauth_verifier")
    state          = request.args.get("state", "")
    code           = request.args.get("code")
    error          = request.args.get("error")

    if oauth_token and oauth_verifier:
        # OAuth 1.0a callback (X/Twitter)
        data = _web_oauth.get("x", {})
        data["done"] = True
        _web_oauth["x"] = data
        try:
            _x_exchange_and_save(oauth_token, oauth_verifier)
        except Exception as e:
            _web_oauth["x"]["exchange_error"] = str(e)
    else:
        # OAuth 2.0 callback (Threads, etc.)
        matched = next((pid for pid, d in _web_oauth.items() if d.get("state") == state), None)
        if matched:
            _web_oauth[matched]["code"]  = code
            _web_oauth[matched]["error"] = error
            _web_oauth[matched]["done"]  = True
            if code and matched == "threads":
                try:
                    _threads_exchange_and_save(code)
                except Exception as e:
                    _web_oauth[matched]["exchange_error"] = str(e)
            elif code and matched == "youtube":
                try:
                    _youtube_exchange_and_save(code)
                except Exception as e:
                    _web_oauth[matched]["exchange_error"] = str(e)
            elif code and matched == "etsy":
                try:
                    _etsy_exchange_and_save(code)
                except Exception as e:
                    _web_oauth[matched]["exchange_error"] = str(e)

    return """<!doctype html><html><body style='font-family:sans-serif;text-align:center;padding:4rem'>
        <h2 style='color:#7C3AED'>&#10003; Authorized!</h2>
        <p style='color:#555'>You can close this tab and return to Socialline.</p>
    </body></html>"""


def _youtube_exchange_and_save(code: str):
    import requests as _req
    client_id     = os.getenv("YOUTUBE_CLIENT_ID") or ""
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET") or ""
    redirect      = "https://app.socialline.space/callback"
    resp = _req.post("https://oauth2.googleapis.com/token", data={
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  redirect,
        "grant_type":    "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    tokens        = resp.json()
    access_token  = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise ValueError("No refresh token returned — revoke app access at myaccount.google.com/permissions and try again")
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), "YOUTUBE_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_FILE), "YOUTUBE_REFRESH_TOKEN", refresh_token)
    load_dotenv(ENV_FILE, override=True)


def _x_exchange_and_save(oauth_token: str, oauth_verifier: str):
    import tweepy as _tweepy
    handler = (_web_oauth.get("x") or {}).get("handler")
    if not handler:
        consumer_key    = os.getenv("X_CONSUMER_KEY") or ""
        consumer_secret = os.getenv("X_CONSUMER_SECRET") or ""
        handler = _tweepy.OAuth1UserHandler(consumer_key, consumer_secret)
    handler.request_token = {"oauth_token": oauth_token, "oauth_token_secret": ""}
    access_token, access_token_secret = handler.get_access_token(oauth_verifier)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), "X_ACCESS_TOKEN",        access_token)
    set_key(str(ENV_FILE), "X_ACCESS_TOKEN_SECRET", access_token_secret)
    load_dotenv(ENV_FILE, override=True)


def _threads_exchange_and_save(code: str):
    import requests as _req
    app_id     = os.getenv("THREADS_APP_ID") or ""
    app_secret = os.getenv("THREADS_APP_SECRET") or ""
    redirect   = "https://app.socialline.space/callback"
    # Short-lived token
    resp = _req.post("https://graph.threads.net/oauth/access_token", data={
        "client_id": app_id, "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect, "code": code,
    }, timeout=15)
    resp.raise_for_status()
    data       = resp.json()
    short_tok  = data["access_token"]
    user_id    = str(data["user_id"])
    # Long-lived token
    resp2 = _req.get("https://graph.threads.net/v1.0/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": short_tok,
    }, timeout=15)
    resp2.raise_for_status()
    long_tok = resp2.json()["access_token"]
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), "THREADS_USER_ID",      user_id)
    set_key(str(ENV_FILE), "THREADS_ACCESS_TOKEN",  long_tok)
    load_dotenv(ENV_FILE, override=True)


def _etsy_exchange_and_save(code: str):
    import requests as _req
    api_key   = os.getenv("ETSY_API_KEY") or ""
    verifier  = (_web_oauth.get("etsy") or {}).get("code_verifier", "")
    redirect  = "https://app.socialline.space/callback"
    resp = _req.post("https://api.etsy.com/v3/public/oauth/token", data={
        "grant_type":    "authorization_code",
        "client_id":     api_key,
        "redirect_uri":  redirect,
        "code":          code,
        "code_verifier": verifier,
    }, timeout=15)
    resp.raise_for_status()
    tokens        = resp.json()
    access_token  = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        raise ValueError("No access token returned from Etsy")
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), "ETSY_ACCESS_TOKEN",  access_token)
    if refresh_token:
        set_key(str(ENV_FILE), "ETSY_REFRESH_TOKEN", refresh_token)
    load_dotenv(ENV_FILE, override=True)


@app.route("/api/accounts/launch-oauth/<platform_id>", methods=["POST"])
@login_required
def api_setup_launch_oauth(platform_id):
    # Flask-native flow for platforms that use app.socialline.space/callback
    if platform_id == "youtube":
        import secrets as _sec
        client_id     = os.getenv("YOUTUBE_CLIENT_ID") or ""
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET") or ""
        if not client_id or not client_secret:
            return jsonify({"error": "Enter and save Client ID and Client Secret first"}), 400
        state    = _sec.token_urlsafe(16)
        redirect = "https://app.socialline.space/callback"
        scopes   = " ".join([
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ])
        params   = urlencode({
            "client_id":     client_id,
            "redirect_uri":  redirect,
            "response_type": "code",
            "scope":         scopes,
            "state":         state,
            "access_type":   "offline",
            "prompt":        "consent",
        })
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
        _web_oauth["youtube"] = {"state": state, "done": False}
        _oauth_account_counts["youtube"] = len(_get_acct_store().list_all("youtube"))
        return jsonify({"ok": True, "auth_url": auth_url,
            "message": "Authorize in the browser window, then click below."})

    if platform_id == "x":
        import tweepy as _tweepy
        consumer_key    = os.getenv("X_CONSUMER_KEY") or ""
        consumer_secret = os.getenv("X_CONSUMER_SECRET") or ""
        if not consumer_key or not consumer_secret:
            return jsonify({"error": "Enter and save Consumer Key and Consumer Secret first"}), 400
        try:
            handler  = _tweepy.OAuth1UserHandler(
                consumer_key, consumer_secret,
                callback="https://app.socialline.space/callback",
            )
            auth_url = handler.get_authorization_url()
            _web_oauth["x"] = {"handler": handler, "done": False}
            _oauth_account_counts["x"] = len(_get_acct_store().list_all("x"))
            return jsonify({"ok": True, "auth_url": auth_url,
                "message": "Authorize in the browser window, then click below."})
        except Exception as e:
            return jsonify({"error": f"Failed to start X auth: {e}"}), 500

    if platform_id == "threads":
        import secrets as _sec
        p = next((x for x in SETUP_PLATFORMS if x["id"] == platform_id), None)
        app_id = os.getenv("THREADS_APP_ID") or ""
        if not app_id:
            return jsonify({"error": "THREADS_APP_ID not set — enter it in the form fields first"}), 400
        state    = _sec.token_urlsafe(16)
        redirect = "https://app.socialline.space/callback"
        params   = urlencode({
            "client_id": app_id, "redirect_uri": redirect,
            "scope": "threads_basic,threads_content_publish",
            "response_type": "code", "state": state,
        })
        auth_url = f"https://threads.net/oauth/authorize?{params}"
        _web_oauth[platform_id] = {"state": state, "done": False}
        _oauth_account_counts[platform_id] = len(_get_acct_store().list_all(platform_id))
        return jsonify({"ok": True, "auth_url": auth_url,
            "message": "Authorize in the browser window, then click below."})

    if platform_id == "etsy":
        import secrets as _sec, hashlib as _hash, base64 as _b64
        api_key = os.getenv("ETSY_API_KEY") or ""
        if not api_key:
            return jsonify({"error": "Enter and save your Etsy API Key first"}), 400
        # PKCE
        verifier  = _sec.token_urlsafe(43)
        digest    = _hash.sha256(verifier.encode()).digest()
        challenge = _b64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        state     = _sec.token_urlsafe(16)
        redirect  = "https://app.socialline.space/callback"
        params    = urlencode({
            "response_type":         "code",
            "redirect_uri":          redirect,
            "scope":                 "listings_w listings_r shops_r",
            "client_id":             api_key,
            "state":                 state,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        })
        auth_url = f"https://www.etsy.com/oauth/connect?{params}"
        _web_oauth["etsy"] = {"state": state, "done": False, "code_verifier": verifier}
        _oauth_account_counts["etsy"] = len(_get_acct_store().list_all("etsy"))
        return jsonify({"ok": True, "auth_url": auth_url,
            "message": "Authorize in the browser window — your Etsy listing access will be connected."})

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
        return jsonify({"ok": True, "pid": proc.pid, "message": "Auth flow started. Check your browser for the OAuth window."})
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

    # Web OAuth flow (Flask-native, e.g. Threads via app.socialline.space/callback)
    web = _web_oauth.get(platform_id)
    if web and web.get("done"):
        web_configured = _platform_status_env_only(p)
        exchange_error = web.get("exchange_error")
        return jsonify({
            "configured": web_configured,
            "oauth_running": False,
            "exchange_error": exchange_error,
        })

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

        # Auto-import into AccountStore when subprocess finished and tokens are fresh
        if tokens_ready and not new_account_saved:
            try:
                load_dotenv(ENV_FILE, override=True)
                publisher = _make_publisher(platform_id)
                if publisher:
                    info = publisher.get_account_info()
                    if info:
                        acct  = _get_acct_store()
                        creds = acct.snapshot_from_env(platform_id)
                        acct.add(platform_id, info["name"], info["id"], creds)
                        _oauth_account_counts.pop(platform_id, None)
            except Exception as _auto_err:
                logger.warning(f"Auto-import failed for {platform_id}: {_auto_err}")

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
            import requests as _req

            # --- Path 1: direct Instagram OAuth already saved tokens to env ---
            env_ig_id    = os.environ.get("INSTAGRAM_ACCOUNT_ID")
            env_ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
            if env_ig_id and env_ig_token:
                ig_resp = _req.get(
                    f"https://graph.instagram.com/v21.0/{env_ig_id}",
                    params={"fields": "id,username", "access_token": env_ig_token},
                    timeout=10,
                )
                username = ig_resp.json().get("username", "") if ig_resp.ok else ""
                acct = _get_acct_store()
                creds = {"INSTAGRAM_ACCOUNT_ID": env_ig_id, "INSTAGRAM_ACCESS_TOKEN": env_ig_token}
                account = acct.add("instagram", f"@{username}" if username else env_ig_id, env_ig_id, creds)
                return jsonify({"ok": True,
                    "message": f"Instagram connected{' as @' + username if username else ''}",
                    "account": account.public_dict()})

            # --- Path 2: derive from Facebook Page credentials ---
            fb_acct = _get_acct_store().get_active("facebook")
            if not fb_acct or not fb_acct.credentials:
                return jsonify({"ok": False, "offer_direct_oauth": True,
                    "message": "Facebook not connected. You can connect Instagram directly instead."})
            fb_creds   = fb_acct.credentials
            page_id    = fb_creds.get("FACEBOOK_PAGE_ID")
            page_token = fb_creds.get("FACEBOOK_PAGE_ACCESS_TOKEN")
            if not page_id or not page_token:
                return jsonify({"ok": False, "offer_direct_oauth": True,
                    "message": "Facebook Page credentials missing. You can connect Instagram directly instead."})
            resp = _req.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"fields": "instagram_business_account,connected_instagram_account", "access_token": page_token},
                timeout=10,
            )
            if not resp.ok:
                return jsonify({"ok": False, "offer_direct_oauth": True,
                    "message": f"Facebook API error — you can connect Instagram directly instead."})
            page_data = resp.json()
            ig_data  = page_data.get("instagram_business_account")
            if not ig_data:
                return jsonify({"ok": False, "offer_direct_oauth": True,
                    "message": "Instagram account not detected via Facebook. "
                               "Click below to log in with Instagram directly."})
            ig_id = ig_data["id"]
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
        "facebook":     ("pipeline.platforms.facebook.publisher",     "FacebookPublisher"),
        "instagram":    ("pipeline.platforms.instagram.publisher",    "InstagramPublisher"),
        "threads":      ("pipeline.platforms.threads.publisher",      "ThreadsPublisher"),
        "x":            ("pipeline.platforms.x.publisher",            "XPublisher"),
        "tiktok":       ("pipeline.platforms.tiktok.publisher",       "TikTokPublisher"),
        "bluesky":      ("pipeline.platforms.bluesky.publisher",      "BlueskyPublisher"),
        "linkedin":     ("pipeline.platforms.linkedin.publisher",     "LinkedInPublisher"),
        "pinterest":    ("pipeline.platforms.pinterest.publisher",    "PinterestPublisher"),
        "youtube":      ("pipeline.platforms.youtube.publisher",      "YouTubePublisher"),
        "telegram":     ("pipeline.platforms.telegram.publisher",     "TelegramPublisher"),
        # New platforms
        "substack":     ("pipeline.platforms.substack.publisher",     "SubstackPublisher"),
        "etsy":         ("pipeline.platforms.etsy.publisher",         "EtsyPublisher"),
        "lemonsqueezy": ("pipeline.platforms.lemonsqueezy.publisher", "LemonSqueezyPublisher"),
        "gumroad":      ("pipeline.platforms.gumroad.publisher",      "GumroadPublisher"),
        "teachable":    ("pipeline.platforms.teachable.publisher",    "TeachablePublisher"),
        "udemy":        ("pipeline.platforms.udemy.publisher",        "UdemyPublisher"),
        # Browser automation
        "medium":         ("pipeline.browser.connectors.medium",         "MediumConnector"),
        "quora":          ("pipeline.browser.connectors.quora",          "QuoraConnector"),
        "tpt":            ("pipeline.browser.connectors.tpt",            "TPTConnector"),
        "substack_notes": ("pipeline.browser.connectors.substack_notes", "SubstackNotesConnector"),
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


# ── Browser automation endpoints ──────────────────────────────────────────────

_browser_login_status: dict[str, dict] = {}  # platform_id → {running, done, error}


@app.route("/api/accounts/browser-login/<platform_id>", methods=["POST"])
@login_required
def api_browser_login_start(platform_id):
    """Start a headed browser session so the user can log in manually."""
    from pipeline.browser.sessions import BrowserSessionStore
    connector = _make_publisher(platform_id, {})
    if connector is None or not hasattr(connector, "first_login"):
        return jsonify({"error": "Not a browser platform"}), 400

    if _browser_login_status.get(platform_id, {}).get("running"):
        return jsonify({"ok": True, "message": "Browser login already in progress"})

    _browser_login_status[platform_id] = {"running": True, "done": False, "error": None}

    def _run():
        try:
            connector.first_login()
            _browser_login_status[platform_id] = {"running": False, "done": True, "error": None}
        except Exception as e:
            _browser_login_status[platform_id] = {"running": False, "done": False, "error": str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Browser opened — log in and it will save automatically"})


@app.route("/api/accounts/browser-login/<platform_id>/status")
@login_required
def api_browser_login_status(platform_id):
    from pipeline.browser.sessions import BrowserSessionStore
    status = _browser_login_status.get(platform_id, {"running": False, "done": False, "error": None})
    has_session = BrowserSessionStore().exists(platform_id)
    return jsonify({**status, "has_session": has_session})


@app.route("/api/accounts/browser-login/<platform_id>", methods=["DELETE"])
@login_required
def api_browser_session_clear(platform_id):
    """Clear a saved browser session (force re-login)."""
    from pipeline.browser.sessions import BrowserSessionStore
    BrowserSessionStore().delete(platform_id)
    return jsonify({"ok": True})


@app.route("/api/browser/policy")
@login_required
def api_browser_policy():
    from pipeline.browser.policy import SafePolicy
    return jsonify(SafePolicy().all_status())


@app.route("/api/browser/policy/<platform_id>/kill", methods=["POST"])
@admin_required
def api_browser_policy_kill(platform_id):
    from pipeline.browser.policy import SafePolicy
    SafePolicy().kill(platform_id)
    return jsonify({"ok": True})


@app.route("/api/browser/policy/<platform_id>/unkill", methods=["POST"])
@admin_required
def api_browser_policy_unkill(platform_id):
    from pipeline.browser.policy import SafePolicy
    SafePolicy().unkill(platform_id)
    return jsonify({"ok": True})


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
    "substack":     ["SUBSTACK_SESSION_TOKEN", "SUBSTACK_PUBLICATION"],
    "etsy":         ["ETSY_ACCESS_TOKEN", "ETSY_REFRESH_TOKEN"],
    "lemonsqueezy": ["LEMONSQUEEZY_API_KEY"],
    "gumroad":      ["GUMROAD_ACCESS_TOKEN"],
    "teachable":    ["TEACHABLE_API_KEY"],
    "udemy":        ["UDEMY_BEARER_TOKEN"],
    "bluesky":   ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "linkedin":  ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN"],
    "pinterest": ["PINTEREST_ACCESS_TOKEN", "PINTEREST_BOARD_ID"],
    "youtube":   ["YOUTUBE_ACCESS_TOKEN", "YOUTUBE_REFRESH_TOKEN"],
    "telegram":  ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
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
    return jsonify(_get_source_store().list_all())


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
    source = _get_source_store().add(name, type_, config, schedule)
    return jsonify({"ok": True, "source": source})


@app.route("/api/sources/<source_id>", methods=["PUT"])
@not_reviewer
def api_sources_update(source_id):
    data = request.get_json()
    fields = {}
    for k in ("name", "config", "enabled", "schedule"):
        if k in data:
            fields[k] = data[k]
    _get_source_store().update(source_id, **fields)
    return jsonify({"ok": True, "source": _get_source_store().get(source_id)})


@app.route("/api/sources/<source_id>", methods=["DELETE"])
@not_reviewer
def api_sources_delete(source_id):
    _get_source_store().delete(source_id)
    return jsonify({"ok": True})


@app.route("/api/sources/<source_id>/pull", methods=["POST"])
@not_reviewer
def api_sources_pull(source_id):
    source = _get_source_store().get(source_id)
    if not source:
        return jsonify({"error": "Source not found"}), 404
    try:
        added = _get_pull_engine().pull(source)
        _get_source_store().log_pull(source_id, len(added))
        return jsonify({"ok": True, "added": len(added), "files": added})
    except Exception as e:
        _get_source_store().log_pull(source_id, 0, status="error", error=str(e))
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sources/<source_id>/log")
@login_required
def api_sources_log(source_id):
    return jsonify(_get_source_store().recent_log(source_id))


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

    image_path = _get_queue_dir() / filename
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

    post_id = _get_sched_store().add(filename, captions, platforms, platform_options, scheduled_at)
    return jsonify({"ok": True, "id": post_id})


@app.route("/api/schedule", methods=["GET"])
@login_required
def api_schedule_list():
    posts = _get_sched_store().list_all()
    return jsonify(posts)


@app.route("/api/schedule/<post_id>", methods=["DELETE"])
@login_required
def api_schedule_delete(post_id):
    _get_sched_store().delete(post_id)
    return jsonify({"ok": True})


# ── Default schedule config ────────────────────────────────────────────────

_DEFAULT_SCHED = {
    "enabled": False,
    "days_of_week": [0, 1, 2, 3, 4, 5, 6],   # 0=Mon … 6=Sun
    "times": ["09:00", "18:00"],
    "days_ahead": 7,
    "platforms": [],
    "trigger": "manual",                # "manual" | "on_upload"
}


def _get_default_sched_file() -> Path:
    b = _get_active_brand()
    if not b:
        abort(409, "No active brand selected")
    f = _brand_dir_for(b["id"]) / "default_schedule.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    return f


def _load_default_sched() -> dict:
    f = _get_default_sched_file()
    if f.exists():
        try:
            return {**_DEFAULT_SCHED, **json.loads(f.read_text())}
        except Exception:
            pass
    return dict(_DEFAULT_SCHED)


def _save_default_sched(cfg: dict):
    f = _get_default_sched_file()
    f.write_text(json.dumps(cfg, indent=2))


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

    if not times and not platforms:
        return jsonify({"error": "Add at least one time and select at least one platform in Default Schedule"}), 400
    if not times:
        return jsonify({"error": "Add at least one posting time in Default Schedule"}), 400
    if not platforms:
        return jsonify({"error": "Select at least one platform in Default Schedule"}), 400

    # Collect already-scheduled filenames so we don't double-schedule
    existing = {p["filename"] for p in _get_sched_store().list_all(status="pending")}

    # Queue items not yet scheduled, oldest first
    queue_items = [
        f.name for f in sorted(_get_queue_dir().iterdir(), key=lambda x: x.stat().st_mtime)
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
    pending = _get_sched_store().list_all(status="pending")
    occupied = {p["scheduled_at"][:16] for p in pending}   # "YYYY-MM-DDTHH:MM"
    free_slots = [s for s in slots if s.strftime("%Y-%m-%dT%H:%M") not in occupied]

    # Pair free slots with queue items
    count = 0
    for slot_dt, filename in zip(free_slots, queue_items):
        captions = {p: Path(filename).stem.replace("_", " ") for p in platforms}
        _get_sched_store().add(
            filename=filename,
            captions=captions,
            platforms=platforms,
            platform_options={},
            scheduled_at=slot_dt.isoformat(timespec="minutes"),
        )
        count += 1

    return jsonify({"ok": True, "scheduled": count,
                    "message": f"{count} post{'s' if count != 1 else ''} scheduled"})


# ── Review page ───────────────────────────────────────────────────────────────

@app.route("/review")
@login_required
def page_review():
    return render_template("review.html")


# ── Render review API ─────────────────────────────────────────────────────────

@app.route("/api/renders/video/<output_id>")
@login_required
def api_render_video(output_id):
    """Stream an MP4 render output by output_id (not file path, for security)."""
    b  = _get_active_brand()
    rs = _render_store_for(b["id"])
    with rs._conn() as conn:
        row = conn.execute(
            "SELECT * FROM render_outputs WHERE id=?", (output_id,)
        ).fetchone()
    if not row:
        return "Not found", 404
    job = rs.get(row["job_id"])
    if not job or job["brand_id"] != b["id"]:
        return "Forbidden", 403
    path = Path(row["file_path"])
    if not path.exists():
        return "File not found on disk", 404
    return send_file(str(path), mimetype="video/mp4", conditional=True)


@app.route("/api/renders/review", methods=["GET"])
@login_required
def api_renders_review():
    """All jobs awaiting user review for the active brand."""
    rs   = _get_render_store()
    jobs = rs.get_pending_review()
    for job in jobs:
        job["outputs"] = rs.get_outputs(job["id"])
        job["captions"] = rs.get_captions(job["id"])
    return jsonify(jobs)


@app.route("/api/renders/history", methods=["GET"])
@login_required
def api_renders_history():
    """Completed and failed render jobs, newest first."""
    rs     = _get_render_store()
    status = request.args.get("status")
    jobs   = rs.list_all(status=status)
    return jsonify(jobs)


@app.route("/api/renders/<job_id>", methods=["GET"])
@login_required
def api_render_get(job_id):
    b  = _get_active_brand()
    rs = _render_store_for(b["id"])
    job = rs.get(job_id)
    if not job or job["brand_id"] != b["id"]:
        return jsonify({"error": "Not found"}), 404
    job["outputs"]  = rs.get_outputs(job_id)
    job["captions"] = rs.get_captions(job_id)
    return jsonify(job)


@app.route("/api/renders/<job_id>/approve", methods=["POST"])
@login_required
def api_render_approve(job_id):
    """Approve a render and auto-schedule using brand's default schedule config."""
    b  = _get_active_brand()
    rs = _render_store_for(b["id"])
    job = rs.get(job_id)
    if not job or job["brand_id"] != b["id"]:
        return jsonify({"error": "Not found"}), 404
    if job["status"] != "pending_review":
        return jsonify({"error": f"Job is '{job['status']}', not pending_review"}), 400

    rs.approve_job(job_id)

    from renderer import VideoRenderer, detect_encoder
    global _detected_encoder
    if _detected_encoder is None:
        _detected_encoder = detect_encoder()

    brand_cfg = _load_brand_video_config(b["id"])
    renderer  = VideoRenderer(b["id"], brand_cfg,
                              data_root=DATA_DIR / "brands",
                              encoder=_detected_encoder)

    outputs  = rs.get_outputs(job_id)
    captions = rs.get_captions(job_id)
    meta     = job.get("meta") or {}
    scheduled = renderer._auto_schedule(job_id, outputs, captions, meta)

    return jsonify({"ok": True, "scheduled": scheduled})


@app.route("/api/renders/<job_id>/approve-schedule", methods=["POST"])
@login_required
def api_render_approve_schedule(job_id):
    """Approve + schedule specific platforms at a user-chosen time."""
    b  = _get_active_brand()
    rs = _render_store_for(b["id"])
    job = rs.get(job_id)
    if not job or job["brand_id"] != b["id"]:
        return jsonify({"error": "Not found"}), 404
    if job["status"] != "pending_review":
        return jsonify({"error": f"Job is '{job['status']}', not pending_review"}), 400

    data         = request.get_json() or {}
    scheduled_at = data.get("scheduled_at", "")
    req_platforms = data.get("platforms", [])

    if not scheduled_at or not req_platforms:
        return jsonify({"error": "scheduled_at and platforms required"}), 400

    try:
        from datetime import datetime as _dt
        slot = _dt.fromisoformat(scheduled_at)
        if slot <= _dt.now():
            return jsonify({"error": "Scheduled time must be in the future"}), 400
    except ValueError:
        return jsonify({"error": "Invalid datetime format"}), 400

    rs.approve_job(job_id)

    outputs  = {o["platform"]: o for o in rs.get_outputs(job_id)}
    captions = rs.get_captions(job_id)
    queue_dir = _brand_dir_for(b["id"]) / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    import shutil as _shutil
    from pathlib import Path as _P

    # Group requested platforms by dimensions so same-size formats share one file
    groups: dict[str, dict] = {}
    for platform in req_platforms:
        out = outputs.get(platform)
        if not out:
            continue
        dims = out["dimensions"]
        _ORIENT = {"1080x1920": "vertical", "1080x1080": "square"}
        key = _ORIENT.get(dims, "horizontal")
        if key not in groups:
            groups[key] = {"file_path": out["file_path"], "platforms": []}
        groups[key]["platforms"].append(platform)

    sched_store = _sched_store_for(b["id"])
    created = []
    for orientation, group in groups.items():
        src = _P(group["file_path"])
        if not src.exists():
            continue
        queue_filename = f"{job_id}_{orientation}.mp4"
        dst = queue_dir / queue_filename
        _shutil.copy2(src, dst)

        group_captions = {
            p: (captions.get(p, {}).get("caption", "") if isinstance(captions.get(p), dict) else "")
            for p in group["platforms"]
        }
        primary = next(iter(group_captions.values()), "")
        if primary:
            (queue_dir / f"{_P(queue_filename).stem}.caption.txt").write_text(
                primary, encoding="utf-8"
            )

        post_id = sched_store.add(
            filename=queue_filename,
            captions=group_captions,
            platforms=group["platforms"],
            platform_options={},
            scheduled_at=slot.isoformat(timespec="minutes"),
        )
        created.append({"post_id": post_id, "filename": queue_filename,
                        "platforms": group["platforms"]})

    if created:
        rs.update_status(job_id, "scheduled")

    return jsonify({"ok": True, "scheduled": created})


@app.route("/api/renders/<job_id>/reject", methods=["POST"])
@login_required
def api_render_reject(job_id):
    b  = _get_active_brand()
    rs = _render_store_for(b["id"])
    job = rs.get(job_id)
    if not job or job["brand_id"] != b["id"]:
        return jsonify({"error": "Not found"}), 404

    data   = request.get_json() or {}
    reason = data.get("reason", "")
    rs.reject_job(job_id, reason)
    return jsonify({"ok": True})


# ── Renderer background thread (Step 9) ───────────────────────────────────────

def _start_renderer(interval: int = 60):
    """
    Checks for pending_render jobs every `interval` seconds.
    One job at a time per brand — Remotion + ComfyUI are resource-intensive.
    """
    import time
    from renderer import VideoRenderer, detect_encoder

    enc = detect_encoder()

    def _loop():
        while True:
            try:
                for brand in _brand_store.list_all():
                    bid = brand["id"]
                    brand_cfg = _load_brand_video_config(bid)
                    if not brand_cfg.get("video_pipeline", {}).get("enabled", False):
                        continue
                    rs  = _render_store_for(bid)
                    job = rs.get_next_pending()
                    if job:
                        _elog.error(  # using error channel so it shows in log even at INFO level
                            f"[renderer] Starting job {job['id']} for brand {bid}"
                        )
                        renderer = VideoRenderer(bid, brand_cfg,
                                                 data_root=DATA_DIR / "brands",
                                                 encoder=enc)
                        renderer.render_job(job)
            except Exception as exc:
                _elog.error(f"[renderer] Thread error: {exc}", exc_info=True)
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="renderer")
    t.start()
    return t


if __name__ == "__main__":
    import webbrowser, threading
    from brands import migrate_famjam

    # ── Ensure users exist ───────────────────────────────────────────────────
    if not _user_store.get("goldengate"):
        _user_store.create("goldengate", "goldengate", "admin")
        print("  [setup] Created admin: goldengate (change password on first login)")
    else:
        _user_store.update_role("goldengate", "admin")

    if not _user_store.get("zstaikova"):
        _user_store.create("zstaikova", "zstaikova", "user")
        print("  [setup] Created user: zstaikova (change password on first login)")

    # ── Run brand migration (no-op if already done) ──────────────────────────
    migrate_famjam(
        brand_store=_brand_store,
        data_dir=DATA_DIR,
        famjam_dir=ROOT / "famjammemes",
        root_accounts_enc=DATA_DIR / "accounts.enc",
    )

    print()
    print("  Socialline")
    print("  https://app.socialline.space  (public)")
    print("  http://localhost:5000         (local)")
    brands = _brand_store.list_all()
    for b in brands:
        print(f"  Brand: {b['name']} ({b['slug']}) → brands/{b['id']}/")
    print()

    # ── Background threads ──────────────────────────────────────────────────
    start_scheduler(_all_brand_scheduler_data)
    start_source_puller(_all_brand_puller_data)
    _start_renderer()

    threading.Timer(1.0, lambda: webbrowser.open("https://app.socialline.space")).start()
    app.run(debug=False, port=5000)
