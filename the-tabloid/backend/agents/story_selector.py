"""Fetch RSS headlines for a channel, enrich top candidates with article bodies,
then ask Seed 2.0 to pick the best story to debate."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import feedparser
import httpx

from ..config import channel_or_raise, settings
from .llm import call_seed2, parse_json

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (TheTabloid/1.0)"}

# How many candidate headlines we enrich with full article text before handing
# the shortlist to Seed 2.0. More = better picks, slower pipeline.
_ENRICH_TOP_N = 10
_BODY_CHARS = 1800  # trimmed to keep prompts reasonable


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


def _fetch_body(url: str) -> str:
    """Download a URL and extract the main article text with trafilatura.

    Returns an empty string on any failure — callers degrade to title + summary.
    Runs synchronously; wrap in asyncio.to_thread for concurrency.
    """
    if not url:
        return ""
    try:
        import trafilatura  # imported lazily so mock mode doesn't require it
    except ImportError:
        return ""
    try:
        with httpx.Client(
            headers=_UA,
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200 or not resp.text:
                return ""
            text = trafilatura.extract(
                resp.text,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            ) or ""
            return text[:_BODY_CHARS]
    except Exception as exc:
        log.debug("body fetch failed for %s: %s", url, exc)
        return ""


async def enrich_with_bodies(
    headlines: list[dict[str, str]],
    limit: int = _ENRICH_TOP_N,
) -> list[dict[str, str]]:
    """For the top `limit` headlines, fetch their article body in parallel threads."""
    if settings().mock:
        return headlines

    shortlist = headlines[:limit]
    bodies = await asyncio.gather(
        *(asyncio.to_thread(_fetch_body, h.get("link", "")) for h in shortlist),
        return_exceptions=True,
    )
    enriched: list[dict[str, str]] = []
    for h, body in zip(shortlist, bodies):
        if isinstance(body, Exception):
            body = ""
        enriched.append({**h, "body": body})
    # Pass through any tail headlines beyond the enrichment window without a body
    enriched.extend(headlines[limit:])
    return enriched


async def select_story(channel: str, headlines: list[dict[str, str]]) -> dict[str, Any]:
    """Use Seed 2.0 to pick the most debatable story.

    Expects headlines to already carry optional `body` fields from
    `enrich_with_bodies()`; falls back to title+summary if body is missing.
    """
    if not headlines:
        raise RuntimeError(f"No headlines available for channel {channel!r}")

    # Build a richer prompt: shortlist with body where available, then a tail
    # of headline-only candidates.
    def _fmt(h: dict[str, str], idx: int) -> str:
        parts = [f"[{idx}] {h.get('title', '').strip()} — {h.get('source', '')}"]
        if h.get("body"):
            parts.append(f"    BODY: {h['body'].strip()}")
        elif h.get("summary"):
            parts.append(f"    SUMMARY: {h['summary'].strip()[:400]}")
        if h.get("link"):
            parts.append(f"    URL: {h['link']}")
        return "\n".join(parts)

    headlines_text = "\n\n".join(_fmt(h, i) for i, h in enumerate(headlines) if h.get("title"))
    channel_label = channel.replace("_", " ")

    prompt = f"""You are the story editor for a news debate channel focused on {channel_label}.

Here are today's top stories. Some include the article body; others only the headline:

{headlines_text}

Select the ONE story most likely to generate genuine, heated debate between
opposing perspectives. Favor stories where the BODY shows real tension,
multiple stakeholders, or a consequential decision in flight.

Avoid:
- Wire reports with no opinion angle
- Stories already resolved with no ongoing controversy
- Stories with no human stakes
- Stories where you only saw the headline and can't verify the tension

Return JSON only:
{{
  "headline": "exact headline",
  "source": "source name",
  "url": "article URL if known",
  "key_facts": ["fact 1 grounded in the body", "fact 2", "fact 3"],
  "angle_a": "first debatable angle",
  "angle_b": "opposing debatable angle",
  "why_now": "why this is debatable today specifically",
  "infographic_data": {{
    "key_stat": "one compelling statistic if available",
    "stat_source": "source of that stat",
    "context": "one sentence of background context"
  }}
}}"""

    raw = await call_seed2(prompt, temperature=0.4, max_tokens=900)
    return parse_json(raw)
