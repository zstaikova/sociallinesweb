# Bluesky Integration Setup

Connect Socialline to a Bluesky account for posting text and images via the AT Protocol.

---

## Prerequisites

- A Bluesky account at bsky.app
- Socialline running at `http://localhost:5000`

---

## No OAuth Required

Bluesky does **not** use OAuth. Authentication is handle + app password. There is no authorization redirect or developer app to register.

---

## Create an App Password

Do **not** use your Bluesky login password in Socialline. Create a dedicated app password:

1. Log into bsky.app
2. Go to **Settings** → **Privacy and Security** → **App Passwords**
3. Click **Add App Password**
4. Name it (e.g. `socialline`) and confirm
5. Copy the generated password — it is shown only once

> **Gotcha — login password vs. app password:** Your Bluesky login password will return a 401 `AuthenticationRequired` error even if it is correct. App passwords are a different credential type generated specifically for third-party app access. They look like `xxxx-xxxx-xxxx-xxxx`.

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to Bluesky
3. Enter your **handle** (e.g. `username.bsky.social` or a custom domain handle) and the **app password** you generated
4. Socialline verifies the credentials and stores them encrypted in the brand's AccountStore

---

## Handle Format

- Default Bluesky handles: `username.bsky.social`
- Custom domain handles: `yourdomain.com` (if you've set up DNS verification on Bluesky)
- Do not include `@` prefix

---

## How Posting Works

Socialline uses the AT Protocol `com.atproto.repo.createRecord` lexicon:
- Text posts use `app.bsky.feed.post`
- Images are uploaded via `com.atproto.repo.uploadBlob` first, then embedded in the post record
- Session auth uses `com.atproto.server.createSession` with handle + app password

---

## Credential Storage

| Key | Value |
|---|---|
| `BLUESKY_HANDLE` | Your Bluesky handle |
| `BLUESKY_APP_PASSWORD` | App password (not login password) |

---

## Gotchas and Known Issues

- **"Invalid identifier or password" (401)** — Almost always means you used your login password instead of an app password. Generate an app password at bsky.app/settings/app-passwords.
- **"Email already taken"** — If trying to create a new account, the email is already registered. Use the existing account's credentials or go through Bluesky's password reset.
- **Handle not found** — If you recently changed your handle, use the new handle. Old handles may not resolve immediately.
- **App password invalidated** — Bluesky can invalidate app passwords if the account is compromised or if you revoke them manually. Generate a new one and reconnect.
- **Custom domain handles** — If using a domain handle, ensure DNS TXT records are properly set before using it in Socialline. The AT Protocol DID resolution must resolve to your account.
