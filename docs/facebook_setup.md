# Facebook Integration Setup

Connect Socialline to a Facebook Page so it can post photos on your Page's timeline.

---

## Prerequisites

- A Facebook account that is an **Admin** of a Facebook Page
- A Meta developer app with the **Pages API** product added
- Socialline running at `http://localhost:5000`

---

## Create a Meta Developer App

1. Go to https://developers.facebook.com → **My Apps** → **Create App**
2. Choose **Other** → **Business** type
3. Fill in app name (e.g. `socialline`) and contact email
4. From the app dashboard → **Add a product** → **Facebook Login for Business** (or **Pages API**)
5. Go to **App Settings → Basic** and note your **App ID** and **App Secret**

Add to `.env`:
```env
FACEBOOK_APP_ID=<app id>
FACEBOOK_APP_SECRET=<app secret>
```

---

## Configure OAuth Redirect URI

1. In your Meta app → **Facebook Login for Business → Settings** (or **Facebook Login → Settings**)
2. Add to **Valid OAuth Redirect URIs**:
   ```
   http://localhost:5000/callback
   ```
3. Save changes

> **Gotcha:** Meta accepts `http://localhost` but rejects `http://127.0.0.1`. Keep the redirect as `localhost`.

---

## Required OAuth Scopes

Socialline requests the following scopes during Facebook OAuth:

| Scope | Why |
|---|---|
| `pages_show_list` | List all Pages the user manages |
| `pages_read_engagement` | Read Page metadata |
| `pages_manage_metadata` | Required for page token refresh |
| `pages_manage_posts` | Publish photos and videos to the Page |
| `business_management` | Access Pages managed via Meta Business Portfolio |

> **Gotcha — Business Portfolio vs. direct Page Admin:** If your Page was assigned through a Meta Business Portfolio (the newer "Business Assets" management UI), `/me/accounts` returns an empty list without `business_management` scope. Socialline includes this scope and falls back to `/me/businesses → /{biz_id}/owned_pages` if needed.

---

## Production App Requirement

**This is the most important operational consideration for Facebook.**

When the Meta app is in **Development mode**, the `FACEBOOK_USER_TOKEN` (long-lived, 60-day) may not carry full page scopes even if the user approved them during OAuth. When the Page Access Token expires and Socialline tries to auto-refresh it via `/me/accounts`, Facebook returns:

```
OAuthException code 190: Any of the pages_read_engagement, pages_manage_metadata,
pages_read_user_content, pages_manage_ads, pages_show_list or pages_messaging
permission(s) must be granted before impersonating a user's page.
```

This means fresh posts work (valid page token) but auto-refresh on expiry fails. The error recurs every ~60 days until the app is moved to production.

**To fix permanently:**

1. Go to [developers.facebook.com](https://developers.facebook.com) → your app → **App Review**
2. Submit for review with these permissions:
   - `pages_manage_posts` *(core publishing)*
   - `pages_read_engagement`
   - `pages_manage_metadata`
   - `pages_show_list`
   - `instagram_content_publish` *(for Instagram via same app)*
3. Once approved, move the app from **Development** to **Live**
4. Re-authenticate all brands via Socialline → Accounts → Facebook → Reconnect

Until production approval: reconnect Facebook every ~60 days when the user token expires.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to Facebook
3. A Meta login window opens — log in and grant permissions
4. Socialline exchanges the code for a long-lived Page Access Token and stores it in the brand's encrypted AccountStore
5. The account card shows the connected Page name

**What happens under the hood:**
- Authorization code → exchanged for a short-lived user token
- User token → exchanged for a long-lived user token (60-day)
- Long-lived user token → Page token fetched from `/me/accounts` (or Business Manager fallback)
- All pages the user manages are stored; the first one is set as active

If you manage multiple Pages, the active one is whichever was first in the API response. You can change it from the Accounts page.

---

## Credential Storage

Credentials are stored encrypted in the brand's AccountStore (not `.env`):

| Key | Value |
|---|---|
| `FACEBOOK_PAGE_ID` | Numeric Page ID |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Long-lived Page Access Token |

The encryption key is derived from `ACCOUNT_MASTER_KEY` (in Windows Credential Manager) + `brand_id` using HKDF + Fernet — credentials are never on disk in plain text.

---

## Gotchas and Known Issues

- **"No Facebook Pages found"** — You either have no Pages, or the Page is in a Business Portfolio and `business_management` scope wasn't granted. Re-run OAuth and make sure to approve all permissions.
- **Token expiry** — Long-lived Page tokens from Business-type apps don't expire as long as the user token is refreshed periodically. If you get 401 errors, disconnect and reconnect via the web UI.
- **Transient errors (code 1 / code 2)** — The publisher retries once after 5 seconds for these. If they persist, check the Page's publishing permissions in Meta Business Suite.
- **4 MB image limit** — The publisher auto-compresses images above 4 MB using Pillow before upload. Original files are not modified.
- **Caption limit** — Facebook captions are truncated to 2,000 characters to avoid API code:1 errors on long posts.
