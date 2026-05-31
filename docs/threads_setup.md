# Threads Integration Setup

Connect Socialline to a Threads account for posting images and text.

---

## Prerequisites

- A Threads account (connected to an Instagram account)
- Facebook integration working (see `facebook_setup.md`) — Threads uses Facebook's CDN for image staging
- Socialline accessible via HTTPS (Threads OAuth requires a secure redirect URI)
- A Meta developer app (can be the same one used for Facebook/Instagram)

---

## HTTPS Requirement

> **Critical:** Threads OAuth does **not** accept `http://` redirect URIs. The callback must be `https://`. Socialline uses `https://app.socialline.space/callback` when running behind Cloudflare Tunnel. If the domain is suspended or unavailable, Threads OAuth cannot be completed.

If `socialline.space` is unavailable, the workaround is to use a GitHub Pages relay page (a static page that reads `?code=` from the URL and POSTs it to localhost). See `threads_setup_github_relay.md` for the old approach.

---

## Meta App Configuration

You can use the same Meta app as Facebook/Instagram, or create a separate one.

1. In your Meta app dashboard → **Add a product** → find **Threads API** → **Set up**
2. Under **Threads API → Settings**:
   - **Valid OAuth Redirect URIs**: `https://app.socialline.space/callback`
   - **Uninstall Callback URL**: `https://app.socialline.space/callback`
3. Under **Threads API → Permissions**, add:
   - `threads_basic` — authentication and reading account info
   - `threads_content_publish` — creating and publishing posts

Add to `.env`:
```env
THREADS_APP_ID=<app id>
THREADS_APP_SECRET=<app secret>
```

---

## Sandbox / Test Mode

While your app is in Development mode, only accounts explicitly added as test users can authorize the app.

1. In Meta developer portal → your app → **App Roles** → **Roles** → **Add People**
2. Add the Threads account's associated Instagram/Facebook email
3. The invited user must **accept** the invite:
   - Go to `https://www.threads.com/settings/website_permissions`
   - Find your app under "Apps with Threads permissions" and accept

> **Gotcha — "The user has not accepted the invite":** The OAuth flow completes but returns this error even if the user is listed in Roles. The user must actively accept the invite at the Threads settings URL above — just being added to Roles is not enough.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to Threads
3. A Threads authorization window opens (requires HTTPS redirect)
4. Approve the permissions
5. Socialline exchanges the code for a long-lived token and stores it in the brand's AccountStore

---

## How Posting Works

Threads image posting requires a publicly accessible image URL (same constraint as Instagram):

1. **Stage the image** — Upload to the connected Facebook Page's CDN as an unpublished photo
2. **Create a Threads container** — `POST /me/threads` with `media_type=IMAGE`, `image_url`, and `text`
3. **Publish the container** — `POST /me/threads_publish` with `creation_id`

> **Gotcha — "no Facebook credentials for image staging":** The Threads publisher needs an active Facebook account connected to the same brand to stage images. Connect Facebook first, then connect Threads. If Facebook is disconnected or tokens expire, Threads image posts will fail.

---

## Credential Storage

| Key | Value |
|---|---|
| `THREADS_USER_ID` | Numeric Threads user ID |
| `THREADS_ACCESS_TOKEN` | Long-lived access token (~60 days) |

Facebook credentials (`FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_ACCESS_TOKEN`) are read from the brand's active Facebook account in AccountStore at publish time.

---

## Token Renewal

Threads long-lived tokens expire after ~60 days. When posting fails with an auth error, disconnect and reconnect the account via the Socialline web UI.

---

## Going Live (Production App Review)

In sandbox/development mode, only test users can authorize the app and only they can see posts. To post publicly:

1. In Meta developer portal → your app → **App Review** → request `threads_content_publish`
2. Provide:
   - Description of how the app uses Threads
   - Screencast of the OAuth flow and content posting
   - Live website with Privacy Policy and Terms of Service
3. Once approved, production tokens work for any Threads user

---

## Gotchas and Known Issues

- **"Insecure Login Blocked"** — The redirect URI is HTTP. Threads requires HTTPS. Ensure Cloudflare Tunnel is active and `socialline.space` is reachable.
- **Domain suspension** — If `socialline.space` is flagged/suspended by a registrar or security vendor, the HTTPS redirect fails at the DNS level. The GitHub Pages relay workaround can bypass this during suspension.
- **Wrong account logged in** — Threads OAuth uses whichever account is logged into Threads in the browser. Ensure you're logged into the correct account before clicking Connect.
- **Password lockout** — Too many failed password attempts on the Threads app locks the account temporarily. Use the "Forgot password" flow on Instagram to reset.
