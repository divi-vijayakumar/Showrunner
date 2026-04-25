"""Environment + channel configuration.

All runtime knobs live here. Channels are hardcoded (6 total) per the MVP spec.
Personas are in `personas.py` so they stay editable without touching this file.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    val = os.getenv(key, default)
    return val.strip() if val else default


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


class Settings:
    # BytePlus ModelArk — one base URL hosts Seed (LLM), Seedance (video),
    # Seedream (image). The region-specific host matters; pick AP-Southeast
    # unless your key was provisioned elsewhere.
    byteplus_api_key: str = _env("BYTEPLUS_API_KEY") or _env("ARK_API_KEY")
    ark_base_url: str = _env("ARK_BASE_URL") or _env(
        "BYTEPLUS_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
    )
    # Kept for back-compat with existing .env files — all default to ark_base_url.
    byteplus_base_url: str = _env("BYTEPLUS_BASE_URL") or _env(
        "ARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
    )
    seed_llm_base_url: str = _env("SEED_LLM_BASE_URL") or _env(
        "ARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
    )
    seed_llm_model: str = _env("SEED_LLM_MODEL") or _env(
        "ARK_MODEL_SEED", "seed-1-6-250915"
    )

    # Image model for persona reference portraits.
    seedream_base_url: str = _env("SEEDREAM_BASE_URL") or _env(
        "ARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
    )
    seedream_model: str = _env("SEEDREAM_MODEL") or _env(
        "ARK_MODEL_SEEDREAM", "seedream-5-0-260128"
    )

    # Video model.
    seedance_model: str = _env("SEEDANCE_MODEL") or _env(
        "ARK_MODEL_SEEDANCE", "dreamina-seedance-2-0-260128"
    )

    # BytePlus TTS
    seed_speech_api_key: str = _env("SEED_SPEECH_API_KEY")
    seed_speech_base_url: str = _env("SEED_SPEECH_BASE_URL", "https://api.byteplus.com/speech/v1")

    # OpenRouter (used by the research agent for Gemini with Google grounding)
    openrouter_api_key: str = _env("OPENROUTER_API_KEY")
    openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_research_model: str = _env("OPENROUTER_RESEARCH_MODEL", "google/gemini-2.5-flash")

    # Firebase
    firebase_project_id: str = _env("FIREBASE_PROJECT_ID")
    firebase_storage_bucket: str = _env("FIREBASE_STORAGE_BUCKET")
    firebase_credentials: str = _env("GOOGLE_APPLICATION_CREDENTIALS", "./firebase-service-account.json")

    # Where the backend is reachable from a browser. Used to build absolute
    # video URLs when we're serving mp4s from disk instead of Firebase Storage.
    public_base_url: str = _env("PUBLIC_BASE_URL", "http://localhost:8000")

    # Persistent data directory for finished media (audio/video). Survives
    # restarts so the user can re-listen / re-watch anything ever generated.
    # Relative paths resolve against the cwd that runs uvicorn.
    tabloid_data_dir: str = _env("TABLOID_DATA_DIR", "./data")

    # Celery
    redis_url: str = _env("REDIS_URL", "redis://localhost:6379/0")

    # Provider toggles — default to BytePlus Seed everywhere since that's the
    # hackathon story. Flip individually when a given Seed surface is blocked.
    #   llm_provider:   byteplus | openrouter
    #   tts_provider:   byteplus | elevenlabs | google
    #   video_provider: byteplus | fal | mock
    #   image_provider: byteplus | fal | mock
    llm_provider: str = _env("LLM_PROVIDER", "byteplus").lower()
    tts_provider: str = _env("TTS_PROVIDER", "byteplus").lower()
    video_provider: str = _env("VIDEO_PROVIDER", "byteplus").lower()
    image_provider: str = _env("IMAGE_PROVIDER", "byteplus").lower()

    # Fallback provider creds (set the ones you're actually using)
    elevenlabs_api_key: str = _env("ELEVENLABS_API_KEY")
    elevenlabs_model: str = _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    google_aistudio_api_key: str = _env("GOOGLE_AISTUDIO_API_KEY") or _env("GOOGLE_API_KEY")
    google_tts_model: str = _env("GOOGLE_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    fal_api_key: str = _env("FAL_KEY") or _env("FAL_API_KEY")
    # Default to Fal-hosted Seedance 2.0 Pro img2video — same BytePlus Seed
    # model, but via Fal's billing (so the ARK account overdue doesn't block us).
    fal_video_model: str = _env(
        "FAL_VIDEO_MODEL", "fal-ai/bytedance/seedance/v1/pro/image-to-video"
    )
    fal_image_model: str = _env("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")

    # Dev
    mock: bool = _bool("TABLOID_MOCK", False)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


# -- Channels ---------------------------------------------------------------

CHANNELS: dict[str, dict[str, Any]] = {
    "india_politics": {
        "label": "India Politics",
        "icon": "🇮🇳",
        "color": "#378ADD",
        # Spread across left ↔ right + national/regional:
        # The Hindu (centrist), NDTV (center-left), Indian Express (center),
        # The Wire (left/progressive), Scroll (left independent),
        # The Print (center), Firstpost (center-right), Swarajya (right).
        "rss_feeds": [
            "https://www.thehindu.com/news/national/feeder/default.rss",
            "https://ndtv.com/rss/politics",
            "https://indianexpress.com/section/india/feed/",
            "https://thewire.in/feed",
            "https://scroll.in/feeds/all.rss",
            "https://theprint.in/feed/",
            "https://www.firstpost.com/rss/india.xml",
            "https://swarajyamag.com/feed",
        ],
        "visual_style": "warm golden tones, Indian urban environments, street level, documentary style, cinematic",
    },
    "technology": {
        "label": "Technology",
        "icon": "⚡",
        "color": "#7F77DD",
        # Geographic spread: US (TechCrunch, Verge, Wired, Ars), UK (Register),
        # Asia (Nikkei Asia), Global South (Rest of World), community (HN).
        "rss_feeds": [
            "https://techcrunch.com/feed/",
            "https://www.theverge.com/rss/index.xml",
            "https://www.wired.com/feed/rss",
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://www.theregister.com/headlines.atom",
            "https://restofworld.org/feed/latest/",
            "https://asia.nikkei.com/rss/feed/nar",
            "https://news.ycombinator.com/rss",
        ],
        "visual_style": "clean minimal aesthetic, tech office environments, blue-white palette, sharp focus, cinematic",
    },
    "ai_updates": {
        "label": "AI Updates",
        "icon": "🤖",
        "color": "#1D9E75",
        # Mix of research (MIT Tech Review, Synced, Hugging Face) and
        # industry/commentary (VentureBeat, The Batch, ImportAI, AIBusiness, AI News).
        "rss_feeds": [
            "https://www.technologyreview.com/topic/artificial-intelligence/feed",
            "https://venturebeat.com/category/ai/feed/",
            "https://huggingface.co/blog/feed.xml",
            "https://artificialintelligence-news.com/feed/",
            "https://www.deeplearning.ai/the-batch/feed/",
            "https://syncedreview.com/feed/",
            "https://aibusiness.com/rss.xml",
            "https://jack-clark.net/feed/",
        ],
        "visual_style": "abstract digital visualization, data streams, cool blue-purple tones, futuristic, cinematic",
    },
    "celebrity": {
        "label": "Celebrity",
        "icon": "⭐",
        "color": "#D85A30",
        # Spread across gossip (TMZ, Page Six), industry (Variety, THR, Deadline),
        # mainstream (People, E!), and UK (BBC Entertainment).
        "rss_feeds": [
            "https://www.tmz.com/rss.xml",
            "https://people.com/feed/",
            "https://variety.com/feed/",
            "https://www.eonline.com/syndication/feeds/rssfeeds/topstories.xml",
            "https://www.hollywoodreporter.com/feed/",
            "https://deadline.com/feed/",
            "https://pagesix.com/feed/",
            "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
        ],
        "visual_style": "high contrast paparazzi style, red carpet, flashbulb lighting, saturated colors, cinematic",
    },
    "geopolitics": {
        "label": "Geopolitics",
        "icon": "🌍",
        "color": "#EF9F27",
        # Geographic + ideological: BBC (UK), Al Jazeera (Qatar), DW (Germany),
        # France 24, The Diplomat (Asia), Foreign Policy (US establishment),
        # Hindustan Times (India), Times of Israel (Middle East).
        "rss_feeds": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://foreignpolicy.com/feed/",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "https://rss.dw.com/rdf/rss-en-world",
            "https://www.france24.com/en/rss",
            "https://thediplomat.com/feed/",
            "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
            "https://www.timesofisrael.com/feed/",
        ],
        "visual_style": "cinematic wide shots, government buildings, flags, desaturated serious tone, documentary",
    },
    "fashion": {
        "label": "Fashion",
        "icon": "👗",
        "color": "#D4537E",
        # Mix of luxury/editorial (Vogue, BoF, WWD), youth (Refinery29, Dazed, The Cut),
        # streetwear (Highsnobiety), industry (Fashionista).
        "rss_feeds": [
            "https://www.vogue.com/feed/rss",
            "https://www.businessoffashion.com/feed/",
            "https://wwd.com/feed/",
            "https://www.refinery29.com/en-us/fashion/rss.xml",
            "https://www.dazeddigital.com/rss",
            "https://www.highsnobiety.com/feed/",
            "https://fashionista.com/.rss/full/",
            "https://www.thecut.com/rss.xml",
        ],
        "visual_style": "editorial photography aesthetic, runway lighting, high fashion, bold color, cinematic",
    },
}


def channel_or_raise(channel_id: str) -> dict[str, Any]:
    if channel_id not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel_id}")
    return CHANNELS[channel_id]
