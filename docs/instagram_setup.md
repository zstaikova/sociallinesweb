# Instagram Integration Setup

Step-by-step guide to connect socialline to an Instagram Business account.

---

## Prerequisites

- A Facebook account with a **Facebook Page**
- An **Instagram Business or Creator account** connected to that Facebook Page
- Facebook integration already working (see `facebook_setup.md`) — Instagram reuses the Facebook Page token for image staging

---

## Connect Instagram to Facebook Page

1. Go to your Facebook Page → **Settings** → **Linked accounts** (or **Instagram**)
2. Connect your Instagram Business/Creator account
3. If your Instagram is Personal, convert it first: Instagram app → Settings → Account → Switch to Professional Account

---

## Create a Meta App (Instagram Direct Login)

Instagram uses a **separate** Meta app from Facebook (required since 2024 API changes).

1. Go to https://developers.facebook.com → **My Apps** → **Create App**
2. Choose **Other** → **Business** type
3. App name: e.g. `socialline-IG`
4. From the app dashboard → **Add Product** → **Instagram**
5. Under Instagram → **API setup with Instagram login**

---

## Generate an Instagram Access Token

1. In Meta Developer dashboard → your Instagram app → **Instagram** → **API setup with Instagram login**
2. Click **Generate token** under your Instagram account
3. Grant requested permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
4. Copy the generated **Access Token**
5. Add yourself as a **Tester** if prompted: App Settings → Roles → Testers → add your Instagram account

---

## Get Your Instagram Account ID

In the Meta Graph API Explorer (https://developers.facebook.com/tools/explorer/):

1. Select your Instagram app
2. Use your Instagram access token
3. Run: `GET /me?fields=id,username`
4. The `id` returned is your Instagram Account ID

Or it's shown directly in the token generation screen.

---

## DNS Fix (if `graph.instagram.com` fails to resolve)

Some ISPs block `graph.instagram.com`. If you get DNS errors, add this to your hosts file:

**Windows** — open PowerShell as Administrator:
```powershell
Add-Content C:\Windows\System32\drivers\etc\hosts "157.240.254.63 graph.instagram.com"
```

**Mac/Linux:**
```bash
echo "157.240.254.63 graph.instagram.com" | sudo tee -a /etc/hosts
```

---

## Configure .env

```env
INSTAGRAM_ACCOUNT_ID=<your instagram account ID>
INSTAGRAM_ACCESS_TOKEN=<your instagram access token>

# Also required (for image staging via Facebook CDN):
FACEBOOK_PAGE_ID=<your facebook page ID>
FACEBOOK_PAGE_ACCESS_TOKEN=<your facebook page access token>
```

---

## Verify

```bash
python cli.py auth instagram
```

Expected output:
```
Instagram auth OK — account: @<username> (<account_id>)
```

---

## Test Posting

```bash
python cli.py run --platforms instagram --dry-run
```

Remove `--dry-run` to post.

**How it works:** Instagram's API requires a public image URL. socialline uploads the image to Facebook's CDN as an unpublished photo first, gets the CDN URL, then creates the Instagram media container and publishes it.

---

## Token Renewal

Instagram tokens generated via the dashboard are long-lived (~60 days). When posting fails with an auth error, regenerate the token in Meta Developer dashboard → Instagram → API setup → Generate token, and update `.env`.
