# socialline — Setup Guides

Platform setup docs for the socialline content pipeline.

## Platforms

| Guide | Purpose |
|-------|---------|
| [facebook_setup.md](facebook_setup.md) | Post to a Facebook Page |
| [instagram_setup.md](instagram_setup.md) | Post to an Instagram Business account |
| [x_setup.md](x_setup.md) | Post to X (Twitter) |
| [tiktok_setup.md](tiktok_setup.md) | Post to TikTok (requires sandbox + certification) |
| [reddit_setup.md](reddit_setup.md) | Reddit as content source |

## Quick Start

1. Complete setup for each platform you want to use
2. Fill in `.env` with credentials
3. Verify all connections:
   ```bash
   python cli.py auth facebook
   python cli.py auth instagram
   python cli.py auth x
   python cli.py auth tiktok
   python cli.py auth reddit
   ```
4. Run a dry-run to test:
   ```bash
   python cli.py run --platforms facebook instagram x tiktok --dry-run
   ```
5. Run for real:
   ```bash
   python cli.py run --platforms facebook instagram x tiktok
   ```

## .env Reference

```env
# Reddit (source)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=socialline/1.0

# Facebook
FACEBOOK_PAGE_ID=
FACEBOOK_PAGE_ACCESS_TOKEN=

# Instagram
INSTAGRAM_ACCOUNT_ID=
INSTAGRAM_ACCESS_TOKEN=

# X (Twitter)
X_CONSUMER_KEY=
X_CONSUMER_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=

# TikTok
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_ACCESS_TOKEN=
TIKTOK_OPEN_ID=
TIKTOK_REFRESH_TOKEN=
```
