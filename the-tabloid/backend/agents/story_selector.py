"""Fetch RSS headlines for a channel and ask Seed 2.0 to pick the best story to debate."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import feedparser

from ..config import channel_or_raise, settings
from .llm import call_seed2, parse_json

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (TheTabloid/1.0)"}


def _parse_one(url: str) -> list[dict[str, str]]:
    try:
        feed = feedparser.parse(url, request_headers=_UA)
    except Exception as exc:
        log.warning("RSS parse failed for %s: %s", url, exc)
        return []
    source = feed.feed.get("title", url)
    out: list[dict[str, str]] = []
    for entry in feed.entries[:3]:
        out.append(
            {
                "title": entry.get("title", ""),
                "summary": (entry.get("summary", "") or "")[:500],
                "source": source,
                "link": entry.get("link", ""),
            }
        )
    return out


async def fetch_headlines(channel: str) -> list[dict[str, str]]:
    """Top 3 headlines per feed, fetched in parallel via threads."""
    cfg = channel_or_raise(channel)
    feeds: list[str] = cfg["rss_feeds"]

    if settings().mock:
        return [
            {
                "title": f"Mock headline {i+1} for {channel}",
                "summary": "Mock summary.",
                "source": "Mock Source",
                "link": "",
            }
            for i in range(6)
        ]

    results = await asyncio.gather(
        *(asyncio.to_thread(_parse_one, url) for url in feeds),
        return_exceptions=True,
    )
    headlines: list[dict[str, str]] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("feed error: %s", r)
            continue
        headlines.extend(r)
    return headlines


async def select_story(channel: str, headlines: list[dict[str, str]]) -> dict[str, Any]:
    """Use Seed 2.0 to pick the most debatable story."""
    if not headlines:
        raise RuntimeError(f"No headlines available for channel {channel!r}")

    headlines_text = "\n".join(
        f"- {h['title']} ({h['source']})" for h in headlines if h.get("title")
    )
    channel_label = channel.replace("_", " ")

    prompt = f"""You are the story editor for a news debate channel focused on {channel_label}.

Here are today's top headlines:
{headlines_text}

Select the ONE story most likely to generate genuine debate between opposing perspectives.

Avoid:
- Wire reports with no opinion angle
- Stories already resolved with no ongoing controversy
- Stories with no human stakes

Return JSON only:
{{
  "headline": "exact headline",
  "source": "source name",
  "key_facts": ["fact 1", "fact 2", "fact 3"],
  "angle_a": "first debatable angle",
  "angle_b": "opposing debatable angle",
  "why_now": "why this is debatable today specifically",
  "infographic_data": {{
    "key_stat": "one compelling statistic if available",
    "stat_source": "source of that stat",
    "context": "one sentence of background context"
  }}
}}"""

    raw = await call_seed2(prompt, temperature=0.4, max_tokens=600)
    return parse_json(raw)
