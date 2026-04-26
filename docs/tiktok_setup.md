# TikTok Integration Setup

Step-by-step guide to connect socialline to TikTok via the Content Posting API.

---

## Prerequisites

### 1. TikTok Developer Account
- Go to https://developers.tiktok.com and sign in with your TikTok account
- Complete developer registration if prompted

### 2. Public Website with Legal Pages
TikTok requires a live website with Privacy Policy and Terms of Service before you can create an app.

Easiest option: GitHub Pages

1. Create a GitHub account and a public repo (e.g. `famjammemes/famjammemes`)
2. Enable GitHub Pages on the repo (Settings → Pages → Deploy from main branch)
3. Add the following files to the repo:
   - `index.html` — app landing page describing what the service does
   - `privacy-policy.html` — privacy policy (can use termsfeed.com to generate)
   - `terms.html` — terms of service

Your site will be live at `https://<username>.github.io/<repo>/`

---

## Create the TikTok App (Production)

1. Go to https://developers.tiktok.com → **Manage Apps** → **Create app**
2. Fill in:
   - **App name**: your brand name
   - **Category**: Entertainment (or appropriate)
   - **Description**: what your app does
   - **Terms of Service URL**: your GitHub Pages terms URL
   - **Privacy Policy URL**: your GitHub Pages privacy policy URL
   - **Platform**: Web → enter your GitHub Pages site URL
3. Under **Products** → **Add products**:
   - Add **Login Kit** → Settings → add Redirect URI: `https://<your-site>/`
   - Add **Content Posting API** → enable **Direct Post**
4. Save the **Client Key** and **Client Secret** from App credentials

---

## Create a Sandbox

TikTok requires sandbox testing before certification. The sandbox has separate credentials.

1. In your app page → **Sandbox** → **Create a Sandbox**
2. Name it (e.g. `famjammemes-sandbox`)
3. Select **Clone from Production** → **Confirm**

### Configure the Sandbox

1. **Credentials** — reveal and copy the sandbox **Client Key** and **Client Secret** (different from production)
2. **Add products**:
   - **Login Kit** → Settings → add Redirect URI: `https://<your-site>/`
   - **Content Posting API** → enable **Direct Post**
3. **Scopes** — the following are automatically included by the products above:
   - `user.info.basic` (via Login Kit)
   - `video.publish` (via Content Posting API)
   - `video.upload` (via Content Posting API)
4. **Sandbox settings → Target Users** → **Add account** → add your TikTok username (required — only authorized accounts can test the sandbox)

---

## Configure .env

```env
TIKTOK_CLIENT_KEY=<sandbox client key>
TIKTOK_CLIENT_SECRET=<sandbox client secret>
```

Leave `TIKTOK_ACCESS_TOKEN` and `TIKTOK_OPEN_ID` blank — they get filled in by the auth script.

---

## Run Authorization

```bash
cd D:\socialline
python auth_tiktok.py
```

**Flow:**
1. Script prints a TikTok auth URL and opens your browser
2. TikTok shows a consent screen — click **Continue**
3. TikTok redirects you to your GitHub Pages site
4. Copy the **full URL** from the browser address bar — it will look like:
   ```
   https://<your-site>/?code=XXXX&state=XXXX
   ```
5. Paste it into the terminal when prompted
6. Script exchanges the code for tokens and saves them to `.env`

**Verify:**
```bash
python cli.py auth tiktok
```

Expected output:
```
TikTok auth OK — account: <display name> (<open_id>)
```

---

## Test Posting

```bash
python cli.py run --platforms tiktok --dry-run
```

Remove `--dry-run` to actually post. In sandbox mode, posts are visible only to the sandbox account and are set to `SELF_ONLY` privacy.

---

## Production Certification (after testing)

1. Switch `.env` credentials back to production Client Key / Secret
2. Re-run `python auth_tiktok.py` with production credentials
3. In TikTok portal → your production app → submit for review
4. Required for review:
   - Live website with Privacy Policy and Terms
   - Demo video showing OAuth flow and content posting
   - Description of how the app uses each scope
5. Once approved, change `privacy_level` in `pipeline/publishers/tiktok.py` from `SELF_ONLY` to `PUBLIC_TO_EVERYONE`

---

## Re-authorization (token expired)

TikTok access tokens expire. When `cli.py auth tiktok` fails, re-run:

```bash
python auth_tiktok.py
```

This overwrites the old token in `.env` with a fresh one.
