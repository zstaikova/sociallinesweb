# Instagram Integration Setup

Connect Socialline to an Instagram Business or Creator account for photo publishing.

---

## Prerequisites

- Facebook integration working (see `facebook_setup.md`) — the **same Meta app** is used for both
- An Instagram **Business or Creator** account (not a Personal account)
- Instagram account linked to your Facebook Page

---

## Link Instagram to Your Facebook Page

Instagram posts go through the Facebook Graph API, so the Instagram account must be associated with a Facebook Page:

1. Go to your Facebook Page → **Settings** → **Linked accounts** (or **Instagram**)
2. Click **Connect account** → log into your Instagram account
3. Confirm the connection

If your Instagram is a Personal account, convert it first:
Instagram app → **Settings** → **Account** → **Switch to Professional Account** → Creator or Business.

> **Gotcha — "Connection confirmed" but API still returns nothing:** After linking, confirm the connection is visible at `facebook.com/settings/?tab=linked_instagram`. If you previously removed Socialline from Instagram's "Apps and websites" settings, the Page-Instagram link may appear broken from the API's perspective even though the UI shows it as connected. Reconnect the accounts in that case.

---

## Meta App Configuration

No separate Instagram app is needed — Socialline uses the **same Meta app** as Facebook. Instagram-specific scopes are added to the same OAuth flow.

Additional scopes beyond Facebook's:

| Scope | Why |
|---|---|
| `instagram_basic` | Read Instagram account info and ID |
| `instagram_content_publish` | Create and publish media containers |

Combined with the Facebook scopes (`pages_show_list`, `pages_manage_posts`, `pages_read_engagement`, `business_management`), the full Instagram OAuth request uses all six scopes.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to Instagram
3. A Meta login window opens — approve all permissions including Instagram ones
4. Socialline finds the Instagram Business/Creator account linked to your Facebook Page and stores the credentials

**What happens under the hood:**
- After OAuth, Socialline calls `GET /{page_id}?fields=instagram_business_account,connected_instagram_account` for each connected Page
- It checks both fields: `instagram_business_account` (Business accounts) and `connected_instagram_account` (Creator accounts)
- The Instagram account ID and username are stored alongside the Facebook credentials

> **Gotcha — only numeric ID shown, no username:** If the username appears as just an ID (e.g., `17841435300935077`), the username lookup failed. Socialline queries `graph.facebook.com/v19.0/{ig_id}?fields=username` — if that returns empty, the account may not be fully linked. Re-linking the accounts in Facebook Page Settings usually fixes it.

---

## How Posting Works

Instagram's API does **not** accept direct file uploads. The flow is:

1. **Stage the image** — Upload to the Facebook Page's photo endpoint as an unpublished photo (`published=false`). This puts the image on Meta's CDN.
2. **Get the CDN URL** — Fetch the staged photo's `images` field to get the largest available URL.
3. **Create a media container** — `POST /{ig_account_id}/media` with `image_url` and `caption`.
4. **Publish the container** — `POST /{ig_account_id}/media_publish` with `creation_id`.

> **Gotcha — all calls use `graph.facebook.com`, not `graph.instagram.com`:** The `graph.instagram.com` endpoint only accepts Instagram-specific user tokens, which were deprecated in December 2024. All Instagram Business API calls (container creation, publishing, account info) must go through `graph.facebook.com` using the Page Access Token.

---

## Credential Storage

Stored encrypted in the brand's AccountStore:

| Key | Value |
|---|---|
| `INSTAGRAM_ACCOUNT_ID` | Numeric Instagram Business account ID |
| `INSTAGRAM_ACCESS_TOKEN` | Facebook Page Access Token (used for all IG API calls) |
| `FACEBOOK_PAGE_ID` | Page ID (needed for image staging) |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Page Access Token (needed for image staging CDN upload) |

---

## Caption Limit

Instagram captions are truncated at 2,200 characters. Hashtags are appended after the caption body (up to 30 tags), formatted as `#tag`.

---

## Gotchas and Known Issues

- **"No Instagram Business account found"** — Either the Instagram account isn't linked to the Facebook Page, or it's a Personal account. Check the Page → Settings → Linked accounts.
- **Container creation 400 "Invalid OAuth access token"** — The publisher is using `graph.instagram.com` instead of `graph.facebook.com`. This was a known bug, now fixed — make sure you're running an up-to-date version.
- **"No images in staging response"** — The image staging upload succeeded but the CDN URL lookup failed. Usually a transient Meta API issue; retry in a few minutes.
- **Threads disconnects Instagram link** — Authorizing a new Threads OAuth sometimes resets Instagram permissions. If Instagram stops posting after a Threads connect, re-link in Facebook Page Settings.
