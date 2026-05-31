# Socialline — Platform Overview

## What Is Socialline?

Socialline is a multi-brand social media automation platform built for creators, educators, and small teams who publish content across multiple channels. It connects a source of content (Reddit communities, RSS feeds, or local media) to a publishing pipeline that distributes posts to Facebook, Instagram, X (Twitter), Threads, Bluesky, LinkedIn, TikTok, and more — with scheduling, per-platform caption editing, and a web-based queue manager.

A single Socialline instance supports multiple **brands** (e.g., a meme page, an educational channel, a product brand), each with its own credentials, content queue, posting schedule, and connected accounts. Brands are fully isolated from one another.

---

## How Platform Connections Work

Each platform uses the authentication method the platform requires:

**Meta (Facebook, Instagram, Threads)** — Socialline uses OAuth 2.0 via the Meta Graph API. The user clicks "Connect" in the Socialline dashboard, which opens a Meta login window. After the user grants permissions (Pages, Instagram publishing, Business Management), Meta returns an authorization code that Socialline exchanges for a long-lived Page Access Token. The token is stored encrypted on the server and refreshed automatically before expiry. Instagram posts go to a Business/Creator account linked to the Facebook Page; Threads uses its own OAuth endpoint on the same Meta infrastructure.

**X (Twitter)** — Socialline uses OAuth 1.0a with the user's app credentials (Consumer Key/Secret) stored in the server's environment, and per-account Access Tokens obtained through the standard three-leg OAuth flow. Users authorize Socialline by clicking "Connect X," which opens a Twitter authorization page tied to whichever X account is currently logged in on the browser.

**Bluesky** — Bluesky uses a simple handle + app-password authentication (not OAuth). Users generate a dedicated app password from their Bluesky account settings and enter it in Socialline's account setup page. No redirect flow is required.

**LinkedIn** — Uses OAuth 2.0 with the LinkedIn developer API. The user connects a Company Page through a standard authorization redirect. Community Management API scopes allow Socialline to post on behalf of a page.

**TikTok** — Content Kit API with OAuth 2.0. After authorization, Socialline uploads video files directly via the TikTok chunked upload endpoint, then polls for publish status.

All credentials are stored in an **encrypted AccountStore** using Fernet symmetric encryption. The encryption key is derived per-brand from a master key held in the OS credential manager (Windows Credential Manager / macOS Keychain), so credentials are never written to disk in plain text.

---

## Customer Experience

1. **Create a brand** — Give it a name (e.g., "Fam Jam Memes"). Socialline creates an isolated workspace for it.

2. **Connect accounts** — Visit the Accounts page and click "Connect" next to each platform. A browser window opens for OAuth authorization (or a form for app passwords). Socialline confirms the account name and stores the token.

3. **Configure a source** — Point Socialline at a subreddit, RSS feed, or folder. Set a pull schedule (hourly, daily, weekly). Socialline fetches new content and places it in the brand's queue.

4. **Review the queue** — The queue page shows all pending images or videos. Each item has an AI-generated caption for every connected platform; captions can be edited inline before posting.

5. **Post now or schedule** — Click "Post Now" to publish immediately across selected platforms, or pick a date/time to schedule it. Scheduled posts are stored and fired by a background thread that checks every 30 seconds.

6. **Track results** — The queue card shows per-platform results (posted / failed) after dispatch. Posted items move out of the queue automatically.

---

## Technical Architecture (Brief)

- **Backend**: Python / Flask, running as a local server or behind Cloudflare Tunnel for HTTPS access.
- **Pipeline**: Source → Content Queue (filesystem) → ContentStore (SQLite) → per-platform Publisher. Each Publisher is a small class that handles auth, media prep, and API calls for one platform.
- **Scheduling**: SQLite-backed ScheduleStore with a daemon thread dispatcher. Auto-pull scheduler refills the queue from sources on a configurable cadence.
- **Multi-brand isolation**: Each brand has its own queue directory, AccountStore, ScheduleStore, and source configuration. The server loads all brands at startup and routes requests by `brand_id`.
- **Media transforms**: Images are resized/compressed per-platform before upload (e.g., Facebook enforces a 4 MB limit; TikTok requires specific video specs via Remotion rendering).
