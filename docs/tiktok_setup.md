# TikTok Integration Setup

Connect Socialline to TikTok for video publishing via the Content Posting API.

---

## Prerequisites

- A TikTok account
- A public website with Privacy Policy and Terms of Service (required by TikTok for app creation)
- Socialline running at `http://localhost:5000`
- Node.js installed (for the Remotion video rendering pipeline)

---

## TikTok App Requirements

TikTok requires a live website before creating a developer app. The easiest option is a GitHub Pages site:

1. Create a public GitHub repo
2. Enable GitHub Pages (Settings → Pages → Deploy from main branch)
3. Add `index.html`, `privacy-policy.html`, and `terms.html`

Your site will be at `https://<username>.github.io/<repo>/`

---

## Create the TikTok App

1. Go to https://developers.tiktok.com → **Manage Apps** → **Create app**
2. Fill in app name, category, description, and your Privacy Policy / Terms URLs
3. Under **Products** → **Add products**:
   - **Login Kit** → Settings → add Redirect URI: `http://localhost:5000/callback`
   - **Content Posting API** → enable **Direct Post**
4. Copy the **Client Key** and **Client Secret** from App credentials

Add to `.env`:
```env
TIKTOK_CLIENT_KEY=<client key>
TIKTOK_CLIENT_SECRET=<client secret>
```

> **Gotcha — Client Key vs Client ID:** TikTok calls their OAuth identifier "Client Key" (not "Client ID" like most OAuth providers). It maps to the standard `client_id` OAuth parameter internally.

---

## Create a Sandbox (Required for Testing)

TikTok requires sandbox testing before certification. The sandbox has **separate credentials** from production.

1. In your app page → **Sandbox** → **Create a Sandbox**
2. Choose **Clone from Production**
3. In the sandbox → **Credentials** — copy the sandbox **Client Key** and **Client Secret** (different from production)
4. Add the sandbox Login Kit redirect URI: `http://localhost:5000/callback`
5. **Sandbox → Target Users** → **Add account** → add your TikTok username

> **Gotcha — sandbox credentials are separate:** Using production Client Key/Secret during testing will fail or post to your real account. Use sandbox credentials during development; switch to production only after TikTok certification.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to TikTok
3. A TikTok authorization window opens
4. Click **Continue** to approve
5. Socialline exchanges the code for tokens and stores them in the brand's AccountStore

**What happens under the hood:**
- OAuth 2.0 PKCE flow to `https://www.tiktok.com/v2/auth/authorize/`
- Code exchanged at `https://open.tiktokapis.com/v2/oauth/token/`
- Access Token and Open ID stored in AccountStore

---

## How Posting Works

TikTok requires video files (not images). Socialline's TikTok pipeline:

1. **Render video** — The Remotion pipeline (`D:\remotion`) takes a queue image and renders it into a short video (slide-style or animated) using `@remotion/renderer`
2. **Initialize upload** — `POST /v2/post/publish/video/init/` with video size, title, and privacy settings
3. **Upload chunks** — Video is uploaded in chunks to TikTok's upload URL
4. **Poll status** — `GET /v2/post/publish/status/fetch/` is polled until status is `SEND_SUCCESS` or an error is returned

---

## Credential Storage

| Location | Keys |
|---|---|
| `.env` | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` |
| AccountStore (encrypted) | `TIKTOK_ACCESS_TOKEN`, `TIKTOK_OPEN_ID`, `TIKTOK_REFRESH_TOKEN` |

---

## Privacy Settings

In sandbox mode, all posts are set to `SELF_ONLY` (visible only to the account owner). After production certification, change in `pipeline/platforms/tiktok/publisher.py`:

```python
"privacy_level": "PUBLIC_TO_EVERYONE"
```

---

## Production Certification

After sandbox testing passes:

1. Switch `.env` to production Client Key / Secret
2. Reconnect TikTok in the web UI with production credentials
3. In TikTok portal → your production app → submit for review
4. Required for review:
   - Live website with Privacy Policy and Terms
   - Demo video of the OAuth flow and content posting
   - Description of scope usage

TikTok review typically takes 5–10 business days.

---

## Token Refresh

TikTok access tokens expire. The publisher attempts to refresh using the Refresh Token before each post. If refresh fails, disconnect and reconnect the account in the web UI.

---

## Gotchas and Known Issues

- **Sandbox target user required** — Only accounts added as Target Users in the sandbox settings can authorize the sandbox app. Attempting to auth with an unregistered account returns an error.
- **Video format requirements** — TikTok requires MP4 or WebM, minimum 3 seconds, specific bitrate and resolution constraints. The Remotion pipeline handles this, but raw image uploads are not supported.
- **Upload timeout** — Large video files can take several minutes to upload and process. The publisher polls status for up to 5 minutes before failing.
- **Certification rejection** — TikTok commonly rejects apps that don't have a clear use case or adequate privacy policy. Make the privacy policy specific to your app's data handling.
