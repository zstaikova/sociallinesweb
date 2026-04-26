# Facebook Integration Setup

Step-by-step guide to connect socialline to a Facebook Page.

---

## Prerequisites

- A Facebook account
- A **Facebook Page** (not a personal profile) — create one at https://www.facebook.com/pages/create

---

## Create a Meta App

1. Go to https://developers.facebook.com → **My Apps** → **Create App**
2. Choose **Other** → **Business** type
3. Fill in app name (e.g. `socialline`) and contact email
4. From the app dashboard, add the **Pages API** product (or it may already be listed)

---

## Get a Page Access Token

### Option A — Meta Graph API Explorer (easiest)

1. Go to https://developers.facebook.com/tools/explorer/
2. Select your app from the dropdown
3. Click **Generate Access Token** → log in and grant permissions
4. Required permissions:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `publish_pages`
5. Click **Get Page Access Token** → select your Page
6. Copy the **Page Access Token**

### Option B — Long-lived token (recommended for production)

Short-lived tokens expire in ~1 hour. Exchange for a long-lived token (60 days):

```bash
curl "https://graph.facebook.com/oauth/access_token?
  grant_type=fb_exchange_token&
  client_id=<APP_ID>&
  client_secret=<APP_SECRET>&
  fb_exchange_token=<SHORT_LIVED_TOKEN>"
```

Then get a Page token from the long-lived user token:

```bash
curl "https://graph.facebook.com/v19.0/me/accounts?access_token=<LONG_LIVED_USER_TOKEN>"
```

Copy the `access_token` for your Page from the response.

---

## Get Your Page ID

In the Graph API Explorer, run:

```
GET /me/accounts
```

Or find it in the Page's **About** section → **Page transparency** → Page ID.

---

## Configure .env

```env
FACEBOOK_PAGE_ID=<your page ID>
FACEBOOK_PAGE_ACCESS_TOKEN=<your page access token>
```

---

## Verify

```bash
python cli.py auth facebook
```

Expected output:
```
Facebook auth OK — page: <Page Name> (<page_id>)
```

---

## Test Posting

```bash
python cli.py run --platforms facebook --dry-run
```

Remove `--dry-run` to post. Images appear as photos on the Facebook Page timeline.

---

## Token Renewal

Page Access Tokens from long-lived user tokens never expire as long as the user token is periodically refreshed. If posting fails with an auth error, re-generate the token via Graph API Explorer and update `.env`.
