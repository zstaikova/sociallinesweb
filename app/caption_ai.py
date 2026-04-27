import os
import json
import anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


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

    message = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
