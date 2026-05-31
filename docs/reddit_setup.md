# Reddit Setup (Content Source)

Reddit is a **read-only content source** — Socialline fetches images from subreddits and queues them for publishing to other platforms. Socialline never posts back to Reddit.

---

## Prerequisites

- A Reddit account
- Socialline running at `http://localhost:5000`

---

## Create a Reddit Script App

1. Go to https://www.reddit.com/prefs/apps → **Create another app**
2. Fill in:
   - **Name**: `socialline` (or any name)
   - **Type**: **script** (for server-side / automated scripts — no user interaction required)
   - **Redirect URI**: `http://localhost` (required by the form, not actually used)
3. Click **Create app**
4. Note the credentials:
   - **Client ID**: the short string shown directly under the app name
   - **Client Secret**: shown as `secret`

> **Gotcha — "script" vs "web app" type:** Script-type apps use application-only auth (no user login required, read-only public data). Web app type requires a user login flow. Use **script** type for Socialline — it gives read access to public subreddits without any OAuth dance.

---

## Configure `.env`

Reddit credentials are stored in `.env` directly (not in AccountStore) because Reddit is a shared content source, not a per-account publisher:

```env
REDDIT_CLIENT_ID=<client ID>
REDDIT_CLIENT_SECRET=<client secret>
REDDIT_USER_AGENT=socialline/1.0
```

The `User-Agent` is sent with every API request. Reddit bans IPs that use generic or missing User-Agent strings.

---

## Configure Sources in the Web UI

Reddit sources are configured per-brand in the web UI:

1. Go to `http://localhost:5000` → select your brand → **Sources**
2. Click **Add Source** → choose **Reddit**
3. Fill in:
   - **Subreddit**: e.g. `memes` (no `r/` prefix)
   - **Sort**: `hot`, `top`, `new`, or `rising`
   - **Min score**: minimum upvotes to include (default: 500 — filters low-quality posts)
   - **Schedule**: how often to pull (hourly, 6h, daily, weekly)

---

## How Fetching Works

- Socialline uses **PRAW** (Python Reddit API Wrapper) in read-only mode
- Posts are fetched from `r/{subreddit}/{sort}` and filtered by score and media type (images only)
- Already-fetched posts are tracked in the ContentStore (SQLite) to prevent duplicates
- New images are saved to the brand's queue directory
- **Queue limit**: if the queue already has 10 or more items, the pull is skipped until items are consumed

---

## Scheduling Auto-Pull

If a source has a pull schedule configured and `trigger: on_upload` is set in the brand's `default_schedule.json`, new items are automatically scheduled for posting after being pulled:

```json
{
  "enabled": true,
  "trigger": "on_upload",
  "platforms": ["facebook", "instagram"],
  "times": ["09:00", "17:00"],
  "days_of_week": [0, 1, 2, 3, 4],
  "days_ahead": 7
}
```

---

## Gotchas and Known Issues

- **Rate limiting** — Reddit's free API allows ~100 requests/minute. Pulling too many subreddits at once can hit this. Socialline staggers pulls across sources.
- **Media-only filtering** — Socialline only fetches posts with direct image URLs (`.jpg`, `.png`, `.gif` links). Text posts, video posts, and gallery posts are skipped.
- **Score threshold** — Setting `min_score` too low floods the queue with low-engagement content. Start at 500–1000 for established subreddits.
- **NSFW subreddits** — NSFW content requires the Reddit account to have NSFW browsing enabled. Script apps without a logged-in user can access NSFW subreddits only if the subreddit is public and the User-Agent is correct.
- **Banned/quarantined subreddits** — Quarantined subreddits require explicit user opt-in and cannot be accessed with application-only auth. You'll get a 403.
