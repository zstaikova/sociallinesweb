"""
Caption generation via Claude API.
Generates platform-specific captions from video scripts.
"""
import json
import os
import urllib.request

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-6"

PLATFORM_RULES = {
    "tiktok": {
        "max_chars": 150,
        "tone":      "conversational, energetic, first person",
        "hashtags":  "3-5 relevant hashtags",
        "notes":     "Hook in first 5 words. Short sentences.",
    },
    "instagram_reel": {
        "max_chars": 2200,
        "tone":      "warm, personal, storytelling",
        "hashtags":  "8-12 hashtags at end",
        "notes":     "Hook first line. Line breaks between paragraphs.",
    },
    "youtube_short": {
        "max_chars": 100,
        "tone":      "curiosity-driven, title-style",
        "hashtags":  "2-3 hashtags",
        "notes":     "Reads like a YouTube title. Intrigue over explanation.",
    },
    "facebook": {
        "max_chars": 500,
        "tone":      "warm, community-focused, parent-to-parent",
        "hashtags":  "2-3 hashtags only",
        "notes":     "Link in caption is fine. Personal and relatable.",
    },
    "linkedin": {
        "max_chars": 700,
        "tone":      "professional but human, data-aware",
        "hashtags":  "3-4 professional hashtags",
        "notes":     "Lead with insight. Teachers and educators audience.",
    },
    "twitter": {
        "max_chars": 280,
        "tone":      "punchy, direct, one clear idea",
        "hashtags":  "1-2 hashtags maximum",
        "notes":     "One idea per tweet. No fluff.",
    },
    "instagram_post": {
        "max_chars": 2200,
        "tone":      "warm, personal, storytelling",
        "hashtags":  "8-12 hashtags at end",
        "notes":     "Hook first line. Line breaks between paragraphs.",
    },
}


def generate_caption(script: str, platform: str, brand_config: dict,
                     tone: str = "warm_bold", pillar: str = "",
                     topic: str = "") -> dict:
    """
    Generate a single platform-specific caption via Claude API.
    Returns dict with 'caption' and 'hashtags' keys.
    """
    seo = brand_config.get("seo_strategy", {})

    # Platform rules: SEO strategy overrides generic defaults
    seo_rules = seo.get("platform_rules", {}).get(platform, {})
    base_rules = PLATFORM_RULES.get(platform, PLATFORM_RULES["facebook"])
    rules = {**base_rules, **seo_rules}

    brand_voice   = brand_config.get("voice",
                        "warm, bold, parent-to-parent. Never academic. Always specific.")
    brand_name    = brand_config.get("name", "")
    brand_website = brand_config.get("website", "")

    # SEO enrichments
    keywords = seo.get("keywords", {}).get(platform, [])
    hashtags = seo.get("hashtags", {}).get(platform, [])
    cta      = seo.get("cta", {}).get(platform, "")

    keywords_block = ""
    if keywords:
        keywords_block = f"\nSEO KEYWORDS TO WEAVE IN NATURALLY:\n" + "\n".join(f"- {k}" for k in keywords)

    hashtags_block = ""
    if hashtags:
        hashtags_block = f"\nHASHTAGS TO USE:\n{' '.join(hashtags)}"

    cta_block = f"\nCALL TO ACTION: {cta}" if cta else f"\nALWAYS END WITH: {brand_website}"

    prompt = f"""You are writing a {platform} caption for {brand_name}.

BRAND VOICE: {brand_voice}
PILLAR: {pillar}
TOPIC: {topic}
TONE: {tone}{keywords_block}{hashtags_block}{cta_block}

VIDEO SCRIPT:
{script}

PLATFORM RULES FOR {platform.upper()}:
- Maximum characters: {rules["max_chars"]}
- Tone: {rules["tone"]}
- Notes: {rules.get("notes", "")}

Return ONLY a JSON object with these fields:
{{
  "caption": "the full caption text including CTA and hashtags",
  "hashtags": ["hashtag1", "hashtag2"]
}}

No preamble. No explanation. JSON only."""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    body = json.dumps({
        "model":      CLAUDE_MODEL,
        "max_tokens": 1000,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=body,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            raw = result["content"][0]["text"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
    except Exception:
        return {
            "caption":  script[:rules["max_chars"]] + (f"\n\n{brand_website}" if brand_website else ""),
            "hashtags": [],
        }


def generate_all_captions(script: str, platforms: list, brand_config: dict,
                           tone: str = "warm_bold", pillar: str = "",
                           topic: str = "") -> dict:
    """Generate captions for all platforms. Returns {platform: {caption, hashtags}}."""
    return {
        platform: generate_caption(script, platform, brand_config, tone, pillar, topic)
        for platform in platforms
    }
