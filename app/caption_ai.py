import os
import json
import requests

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

_api_key = None


def _get_api_key():
    global _api_key
    if _api_key is None:
        _api_key = os.environ["DEEPSEEK_API_KEY"]
    return _api_key


def generate_platform_captions(core_caption: str) -> dict:
    """
    Takes a core caption and returns platform-specific versions.
    Returns dict with keys: facebook, instagram, threads, x, tiktok, bluesky, linkedin, pinterest
    """
    prompt = f"""You are a social media content creator for @famjammemes — a family and parenting humor account.

Core caption: "{core_caption}"

Generate platform-specific captions. Respond ONLY with valid JSON, no explanation.

Rules:
- FACEBOOK: Warm and conversational, 1-3 sentences, emojis optional, no hashtag pressure
- INSTAGRAM: Short punchy first line, blank line, then 12-15 relevant hashtags from: #parenting #momlife #dadlife #kidsofinstagram #familyfun #toddlerlife #momhumor #dadhumor #parentinghumor #kidssaythedarndestthings #momofboys #momofgirls #boymom #girlmom #realparenting #parentlife #familylife #funnykids #kidslogic #wholesomememes
- THREADS: Conversational and casual, 1-2 sentences, feels like a tweet but warmer, 2-3 hashtags max, under 500 chars
- X: Max 240 chars, punchy and shareable, 1-2 hashtags max
- TIKTOK: Under 100 chars, hook style, 4-5 hashtags: #parenting #familyfun #momsoftiktok #dadsoftiktok #kidsoftiktok
- BLUESKY: Casual and community-feel, under 280 chars, no hashtags needed (optional 1-2)
- LINKEDIN: Warm professional tone, relatable parenting moment framed as a life observation, 2-3 short paragraphs, no hashtags
- PINTEREST: Descriptive and searchable, 1-2 sentences as a pin description, include keywords like parenting, kids, family, humor

{{
  "facebook": "...",
  "instagram": "...",
  "threads": "...",
  "x": "...",
  "tiktok": "...",
  "bluesky": "...",
  "linkedin": "...",
  "pinterest": "..."
}}"""

    resp = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {_get_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500,
        },
        timeout=30,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def generate_article_captions(bullet_summary: str, article_meta: dict = None) -> dict:
    """
    Takes a bullet-point article summary and returns platform-specific captions.
    Different tone and structure from meme captions — informative, not humorous.
    """
    meta    = article_meta or {}
    title   = meta.get("title", "")
    url     = meta.get("url", "")
    source  = f"\nOriginal article: {url}" if url else ""

    prompt = f"""You are a social media manager. Adapt the following article summary into platform-specific posts.

Article summary:
{bullet_summary}
{source}

Generate platform-specific posts. Respond ONLY with valid JSON, no explanation.

Rules:
- FACEBOOK: Conversational and engaging, 2-3 sentences expanding on the key points, end with a question or CTA, emojis welcome
- INSTAGRAM: Keep the bullet points, add a blank line, then 10-12 relevant hashtags matching the topic
- THREADS: Pick the most interesting bullet point, turn it into a punchy 1-2 sentence hook, 2-3 hashtags, under 500 chars
- X: Most newsworthy bullet in under 240 chars, add source URL if available, 1-2 hashtags
- TIKTOK: Hook-style opener ("Did you know..."), then 2-3 key points condensed, 4-5 hashtags
- BLUESKY: Informative and neutral, 2-3 sentences, bullet points welcome, under 300 chars
- LINKEDIN: Professional tone, open with why this matters to professionals, use the bullets as supporting points, close with a takeaway, 3 short paragraphs, no hashtags
- PINTEREST: Descriptive and keyword-rich, 1-2 sentences summarising what readers will learn, focus on the value

{{
  "facebook": "...",
  "instagram": "...",
  "threads": "...",
  "x": "...",
  "tiktok": "...",
  "bluesky": "...",
  "linkedin": "...",
  "pinterest": "..."
}}"""

    resp = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {_get_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1800,
        },
        timeout=30,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
