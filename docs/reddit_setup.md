# Reddit Setup (Content Source)

Reddit is the **content source** — socialline fetches top memes from subreddits and publishes them to social platforms.

---

## Create a Reddit App

1. Go to https://www.reddit.com/prefs/apps → **Create another app**
2. Fill in:
   - **Name**: `socialline` (or any name)
   - **Type**: **script** (for personal use / automated scripts)
   - **Redirect URI**: `http://localhost` (required, not used)
3. Click **Create app**
4. Note the credentials:
   - **Client ID**: shown under the app name (short string)
   - **Client Secret**: shown as `secret`

---

## Configure .env

```env
REDDIT_CLIENT_ID=<client ID>
REDDIT_CLIENT_SECRET=<client secret>
REDDIT_USER_AGENT=socialline/1.0
```

---

## Verify

```bash
python cli.py auth reddit
```

Expected output:
```
Reddit auth OK — read-only access confirmed
```

---

## Usage

Reddit credentials are used read-only — socialline fetches public posts, no posting back to Reddit.

Default subreddits: `memes`, `dankmemes`

Override at runtime:
```bash
python cli.py run --subreddits memes dankmemes me_irl --platforms facebook
```

---

## Notes

- Reddit's free API allows ~100 requests/minute for script apps
- `min_score` filters out low-quality posts (default: 500 upvotes)
- `--sort hot` (default) fetches currently trending posts
- Already-posted items are tracked in the content store to avoid reposts
