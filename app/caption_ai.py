import os
import base64
import json
import requests
from pathlib import Path

DEEPSEEK_API_URL  = "https://api.deepseek.com/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

_deepseek_key  = None
_anthropic_key = None


def _get_deepseek_key():
    global _deepseek_key
    if _deepseek_key is None:
        _deepseek_key = os.environ["DEEPSEEK_API_KEY"]
    return _deepseek_key


def _get_anthropic_key():
    global _anthropic_key
    if _anthropic_key is None:
        _anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    return _anthropic_key


def describe_image(image_path: Path) -> str:
    """
    Use Claude Haiku vision to describe what a meme image shows.
    Returns a 1-2 sentence plain-English description, or "" on failure.
    """
    key = _get_anthropic_key()
    if not key or not image_path or not image_path.exists():
        return ""

    ext = image_path.suffix.lower().lstrip(".")
    media_type = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    }.get(ext, "image/jpeg")

    image_data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_data},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe what this meme shows in 1-2 plain sentences. "
                            "Focus on the humor or relatable parenting/family moment. "
                            "Don't say 'This meme shows' — just describe it directly. "
                            "Keep it under 60 words."
                        ),
                    },
                ],
            }],
        },
        timeout=20,
    )
    if not resp.ok:
        return ""
    return resp.json()["content"][0]["text"].strip()


def generate_platform_captions(core_caption: str, image_path: Path = None) -> dict:
    """
    Takes a core caption and returns platform-specific versions.
    If image_path is provided, uses Claude vision to describe the image first.
    Returns dict with keys: facebook, instagram, threads, x, tiktok, bluesky, linkedin, pinterest
    """
    # Try vision description first; fall back to the text caption
    description = ""
    if image_path:
        description = describe_image(image_path)

    if description:
        context = f'What the meme shows: "{description}"'
        if core_caption:
            context += f'\nOriginal title (for extra context, may be inaccurate): "{core_caption}"'
    else:
        context = f'Core caption: "{core_caption}"'

    prompt = f"""You are a social media content creator for @famjammemes — a family and parenting humor account.

{context}

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
            "Authorization": f"Bearer {_get_deepseek_key()}",
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
            "Authorization": f"Bearer {_get_deepseek_key()}",
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
