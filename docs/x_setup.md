# X (Twitter) Integration Setup

Step-by-step guide to connect socialline to an X account.

---

## Prerequisites

- An X (Twitter) account
- A phone number verified on the account (required for developer access)

---

## Create an X Developer App

1. Go to https://developer.x.com → **Sign in** → **Developer Portal**
2. Apply for a developer account if you don't have one (answer the use-case questions — "automating content posting for my brand page")
3. Once approved, go to **Projects & Apps** → **New Project**
4. Create a project, then create an **App** inside it
5. App name: e.g. `socialline`

---

## Configure App Permissions

1. In your app → **Settings** tab
2. Under **User authentication settings** → **Set up**
3. Set:
   - **App permissions**: Read and write
   - **Type of App**: Web App, Automated App or Bot
   - **Callback URI**: `http://localhost` (required field, not actually used)
   - **Website URL**: your site URL
4. Save

---

## Get Access Keys

1. Go to your app → **Keys and Tokens** tab
2. Generate / copy:
   - **API Key** (Consumer Key)
   - **API Key Secret** (Consumer Secret)
3. Under **Authentication Tokens** → **Access Token and Secret** → **Generate**
   - Make sure it says **Read and Write** (not Read Only)
4. Copy:
   - **Access Token**
   - **Access Token Secret**

---

## Configure .env

```env
X_CONSUMER_KEY=<API Key>
X_CONSUMER_SECRET=<API Key Secret>
X_ACCESS_TOKEN=<Access Token>
X_ACCESS_TOKEN_SECRET=<Access Token Secret>
```

---

## Verify

```bash
python cli.py auth x
```

Expected output:
```
X auth OK — account: @<username> (<user_id>)
```

---

## Test Posting

```bash
python cli.py run --platforms x --dry-run
```

Remove `--dry-run` to post.

Posts are tweets with the image attached. Caption is truncated to 280 characters with up to 3 hashtags.

---

## Notes

- X uses **tweepy** internally: v2 API for posting, v1.1 API for media uploads
- Access tokens don't expire unless you revoke them or regenerate them
- Free tier allows up to 1,500 tweets/month (Basic plan: 3,000/month)
- If you get a 403 error, check that the Access Token was generated **after** setting Read and Write permissions (tokens generated before the permission change are read-only)
