# X (Twitter) Integration Setup

Connect Socialline to an X account for posting tweets with images.

---

## Prerequisites

- An X (Twitter) account with a verified phone number (required for developer access)
- An X developer app with Read and Write permissions
- Socialline running at `http://localhost:5000`

---

## Create an X Developer App

1. Go to https://developer.x.com → **Developer Portal** → apply for a developer account if needed
2. Create a **Project** and an **App** inside it
3. App name: e.g. `socialline`

---

## Configure App Permissions (do this FIRST)

**Critical:** App permissions must be set **before** generating access tokens. Tokens inherit the permission level at the time they are generated — tokens created under Read-only permission remain read-only even if you upgrade the app later.

1. In your app → **Settings** → **User authentication settings** → **Set up**
2. Set:
   - **App permissions**: Read and Write
   - **Type of App**: Web App, Automated App or Bot
   - **Callback URI / Redirect URL**: `http://127.0.0.1:5000/callback`
   - **Website URL**: your site URL (e.g. `https://socialline.space`)
3. Save

> **Gotcha — callback must be `127.0.0.1`, not `localhost`:** X validates the callback URL strictly. `http://localhost:5000/callback` is rejected with a 403 "Callback URL not approved" error even if you add it to the portal. Add `http://127.0.0.1:5000/callback` specifically. If the server runs on a different port, update accordingly.

---

## Get App Credentials

1. Go to your app → **Keys and Tokens** tab
2. Under **Consumer Keys** → **API Key and Secret** → copy:
   - **API Key** → `X_CONSUMER_KEY`
   - **API Key Secret** → `X_CONSUMER_SECRET`

Add to `.env`:
```env
X_CONSUMER_KEY=<API Key>
X_CONSUMER_SECRET=<API Key Secret>
```

> **These stay in `.env`** — Consumer Key and Secret are app-level credentials, shared across all brands using the same X developer app. Per-account Access Tokens are stored in the AccountStore, not `.env`.

> **Do not revoke or regenerate Consumer Keys without updating `.env`** — and keep `.env` read-only (`attrib +R .env`) to prevent the app from accidentally overwriting it.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to X
3. A browser window opens to X's authorization page — **make sure you are logged into the correct X account in that browser before clicking Connect**
4. Authorize the app; X redirects to `http://127.0.0.1:5000/callback`
5. Socialline exchanges the OAuth verifier for an Access Token and Access Token Secret, stores them in the brand's AccountStore

**What happens under the hood:**
- X OAuth 1.0a three-legged flow via `tweepy.OAuth1UserHandler`
- Request token obtained, user redirected to `https://api.twitter.com/oauth/authorize`
- After user approves, callback delivers `oauth_token` + `oauth_verifier`
- Verifier exchanged for Access Token + Secret, saved to AccountStore

---

## Credential Storage

| Location | Keys |
|---|---|
| `.env` (read-only) | `X_CONSUMER_KEY`, `X_CONSUMER_SECRET` |
| AccountStore (encrypted) | `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` |

Disconnecting an X account in the web UI clears only the AccountStore tokens, never the Consumer Key/Secret.

---

## How Posting Works

- Media uploads use the **v1.1 API** (`media/upload`)
- Tweet creation uses the **v2 API** (`POST /2/tweets`)
- Caption is truncated to 280 characters with up to 3 hashtags appended

---

## Rate Limits

| Plan | Monthly tweet limit |
|---|---|
| Free | 1,500 |
| Basic ($100/mo) | 3,000 |
| Pro ($5,000/mo) | 300,000 |

---

## Gotchas and Known Issues

- **"X app not configured on this server"** — `X_CONSUMER_KEY` is empty in `.env`. The server reads this at startup via `load_dotenv`. If `.env` was recently edited or regenerated, restart the server.
- **"Callback URL not approved" (403)** — The callback registered in the X portal doesn't match what Socialline sends. Verify `http://127.0.0.1:PORT/callback` is listed in the app's authentication settings exactly.
- **Posting to wrong account** — X OAuth authorizes whichever account is currently logged in the browser. Log into the correct account at twitter.com before clicking Connect in Socialline.
- **Read-only token** — If posts fail with a 403 "Write permission" error, the Access Token was generated before app permissions were set to Read and Write. Delete the token in the X portal, set permissions to Read and Write, regenerate, and reconnect.
- **Token doesn't expire** — X Access Tokens are permanent unless revoked. You won't be prompted to reconnect unless you revoke them in the X portal or disconnect in Socialline.
