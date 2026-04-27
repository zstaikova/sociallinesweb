# Threads Integration Setup

Step-by-step guide to connect socialline to Threads via the Threads API.

---

## Prerequisites

- A Threads account (personal account works — no Business account required)
- Facebook integration already working (Meta apps share the same developer portal)
- GitHub Pages site live at `https://famjammemes.github.io/famjammemes/` with the OAuth relay script in `index.html` (see `temp_index.html` in project root)

---

## Create the Meta App

1. Go to https://developers.facebook.com → **My Apps** → **Create App**
2. Select **Other** → **Next** → choose app type **None** → **Next**
3. Fill in:
   - **App Name**: your brand name (e.g. `socialline`)
   - **App Contact Email**: your email
4. Click **Create App**

---

## Add Threads Product

1. In your app dashboard → **Add a product** → find **Threads API** → **Set up**
2. Under **Threads API → Settings**:
   - **Threads Display Name**: your brand (e.g. `socialline`)
   - **Valid OAuth Redirect URIs**: `https://famjammemes.github.io/famjammemes/`
   - **Uninstall Callback URL**: `https://famjammemes.github.io/famjammemes/`
   - **Delete Callback URL**: `https://famjammemes.github.io/famjammemes/`
3. Save changes

---

## Add Required Scopes

In **Threads API → Permissions**:
- Add `threads_basic`
- Add `threads_content_publish`

Both are needed — `threads_basic` for auth, `threads_content_publish` for posting.

---

## Add Test User (Sandbox Mode)

Before going live, Meta requires you to authorize test users.

1. In the developer portal → your app → **App Roles** → **Roles** → **Add People** — OR —
2. Go directly to https://www.threads.com/settings/website_permissions
   - Find your app under **"Apps with Threads permissions"**
   - Accept the pending invite there

The test user must be added before running the auth script — otherwise you'll get:
```
"The user has not accepted the invite to test the app."
```

---

## Configure .env

```env
THREADS_APP_ID=<your app id>
THREADS_APP_SECRET=<your app secret>
```

Leave `THREADS_USER_ID` and `THREADS_ACCESS_TOKEN` blank — filled in by the auth script.

App ID and Secret are found in the developer portal → your app → **App Settings → Basic**.

---

## Run Authorization

```bash
cd D:\socialline
python bin/auth/threads.py
```

**Flow:**
1. Script opens your browser to the Threads consent screen
2. You approve the permissions
3. Threads redirects to your GitHub Pages site with `?code=` in the URL
4. The page's JavaScript automatically relays the code to `localhost:8080`
5. Script catches it, exchanges for a long-lived token, saves to `.env`

**Verify:**
```bash
python bin/cli.py auth threads
```

---

## Test Posting

```bash
python bin/cli.py run --platforms threads --dry-run
```

Remove `--dry-run` to actually post. In sandbox/test mode, only test users can see posts.

---

## Going Live (App Review)

Threads requires app review before posting to real public accounts.

1. In the developer portal → your app → **App Review** → request `threads_content_publish`
2. Provide:
   - Description of how the app uses Threads
   - Screencast demo of the OAuth flow and posting
   - Live website with Privacy Policy and Terms of Service
3. Once approved, re-run `python bin/auth/threads.py` to get a production token

---

## Re-authorization (token expired)

Threads long-lived tokens last ~60 days. When `cli.py auth threads` fails, re-run:

```bash
python bin/auth/threads.py
```
