# Socialline — Setup Guides

Platform setup docs for the Socialline content pipeline.

All platform connections are made through the **web UI** at `http://localhost:5000` → select a brand → **Accounts**. Credentials are stored encrypted in the brand's AccountStore, not in `.env`.

`.env` holds only app-level keys that are shared across brands (API keys, app secrets). It should be kept **read-only** (`attrib +R .env`) to prevent accidental overwrites.

---

## Platforms

| Guide | Auth Method | Notes |
|---|---|---|
| [facebook_setup.md](facebook_setup.md) | OAuth 2.0 (Meta) | Required for Instagram and Threads image staging |
| [instagram_setup.md](instagram_setup.md) | OAuth 2.0 (Meta) | Requires Facebook Page linked to Instagram account |
| [threads_setup.md](threads_setup.md) | OAuth 2.0 (Meta) | Requires HTTPS redirect; depends on Facebook credentials |
| [x_setup.md](x_setup.md) | OAuth 1.0a | Consumer Key in `.env`; Access Token in AccountStore |
| [bluesky_setup.md](bluesky_setup.md) | App password | No OAuth — handle + app password only |
| [linkedin_setup.md](linkedin_setup.md) | OAuth 2.0 | Requires Company Page; Community Management API |
| [tiktok_setup.md](tiktok_setup.md) | OAuth 2.0 | Video only; sandbox required before certification |
| [reddit_setup.md](reddit_setup.md) | Script app (read-only) | Content source, not a publisher |

---

## `.env` Reference

Only app-level credentials belong in `.env`. Per-account tokens are in AccountStore.

```env
# Meta (Facebook / Instagram / Threads)
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=

# Threads (can use the same app or a separate one)
THREADS_APP_ID=
THREADS_APP_SECRET=

# X (Twitter) — app-level only; access tokens go in AccountStore
X_CONSUMER_KEY=
X_CONSUMER_SECRET=

# TikTok
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=

# LinkedIn
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=

# Reddit (content source)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=socialline/1.0

# Server encryption
ACCOUNT_MASTER_KEY=  # stored in Windows Credential Manager, not .env
```

---

## General Gotchas

- **Meta OAuth redirect** — Use `http://localhost:PORT/callback` (not `127.0.0.1`) for all Meta platforms (Facebook, Instagram, Threads).
- **X OAuth redirect** — Use `http://127.0.0.1:PORT/callback` (not `localhost`) for X — X rejects `localhost`.
- **Threads requires HTTPS** — The Threads OAuth redirect must be an `https://` URL. Meta enforces this even in development mode for Threads specifically.
- **AccountStore encryption** — The master key is derived from `ACCOUNT_MASTER_KEY` stored in Windows Credential Manager (not in `.env`). If the Credential Manager entry is missing, AccountStore cannot decrypt credentials.
