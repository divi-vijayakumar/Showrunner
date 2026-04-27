"""Research agent.

Given a selected story + the pool of sibling RSS headlines for the channel,
produces a briefing document the debate panelists can cite. Two-stage loop:

  1. PLAN — Gemini (via OpenRouter) reads the story and names 3–5 questions
     a sharp reporter would chase before going on air.
  2. SYNTHESIS — we pull related RSS entries (body-enriched where available)
     that match those questions, then Gemini writes a tight briefing with
     anchor facts, counterpoints, and pull quotes.

We try Google-search grounding via OpenRouter's tool-call passthrough so
Gemini can reach beyond our RSS pool; if that isn't available on the tier
in use, we degrade to pool-only grounding and keep going.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import settings
from ..sdk.providers.llm import parse_json

log = logging.getLogger(__name__)


# Concrete shape of what this agent returns. Keep in lockstep with
# debate_engine.build_system_prompt which splices `briefing` into prompts.
Briefing = dict[str, Any]


async def _call_gemini(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 1200,
    use_grounding: bool = False,
) -> str:
    """Call Gemini through OpenRouter. Returns the assistant text.

    Raises in mock mode too — mock grounding is handled by `research_story`.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": settings().openrouter_research_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # OpenRouter exposes Gemini's Google-search grounding as a server-side tool.
    # The passthrough name is stable for Gemini 2.x models; older models ignore it.
    if use_grounding:
        payload["tools"] = [{"google_search": {}}]

    headers = {
        "Authorization": f"Bearer {settings().openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://tabloid.local",
        "X-Title": "The Tabloid",
    }
    url = f"{settings().openrouter_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


def _match_related(
    questions: list[str],
    pool: list[dict[str, str]],
    picked_url: str,
    per_question: int = 2,
) -> list[dict[str, str]]:
    """Pick a handful of pool headlines likely to address each question, based
    on keyword overlap. Deliberately dumb — Gemini does the real matching in
    the synthesis stage, we just narrow the input."""
    def _words(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower())}

    picked: list[dict[str, str]] = []
    used: set[str] = set()
    for q in questions:
        q_words = _words(q)
        scored = []
        for h in pool:
            if h.get("link") == picked_url:
                continue
            t_words = _words(h.get("title", "")) | _words(h.get("summary", ""))
            overlap = len(q_words & t_words)
            if overlap:
                scored.append((overlap, h))
        scored.sort(key=lambda x: -x[0])
        for _, h in scored[:per_question]:
            key = h.get("link") or h.get("title")
            if key and key not in used:
                used.add(key)
                picked.append(h)
    return picked


def _format_related(related: list[dict[str, str]]) -> str:
    if not related:
        return "(no closely-related sibling articles found)"
    blocks = []
    for i, h in enumerate(related):
        body = h.get("body") or h.get("summary") or ""
        blocks.append(
            f"[{i}] {h.get('title','')} — {h.get('source','')}\n"
            f"    URL: {h.get('link','')}\n"
            f"    TEXT: {body[:1200].strip()}"
        )
    return "\n\n".join(blocks)


def _mock_briefing(story: dict[str, Any]) -> Briefing:
    return {
        "headline": story.get("headline", ""),
        "questions": [
            "What is the single most contested claim in this story?",
            "Who benefits if this goes the way the provocateur argues?",
            "What historical precedent would the analyst cite?",
        ],
        "anchor_facts": [
            {"claim": "Mock anchor fact 1", "source": "The Tabloid Mock Wire"},
            {"claim": "Mock anchor fact 2", "source": "The Tabloid Mock Wire"},
            {"claim": "Mock anchor fact 3", "source": "The Tabloid Mock Wire"},
        ],
        "counterpoints": [
            "A credible opposing view would point to structural incentives.",
            "Another line of critique questions the underlying methodology.",
        ],
        "pull_quotes": [
            {"quote": "This is the kind of decision that defines a decade.", "attributed_to": "sector analyst"},
        ],
        "fresh_data": [],
        "grounded": False,
    }


async def research_story(
    channel: str,
    story: dict[str, Any],
    related_pool: list[dict[str, str]],
) -> Briefing:
    """Produce a research briefing for the debate panelists."""
    if settings().mock or not settings().openrouter_api_key:
        if not settings().mock:
            log.warning(
                "OPENROUTER_API_KEY not set — research agent degrading to mock briefing"
            )
        return _mock_briefing(story)

    # --- Stage 1: plan -----------------------------------------------------
    plan_prompt = f"""You are a senior producer prepping tonight's {channel.replace('_',' ')} debate on:

HEADLINE: {story.get('headline')}
ANGLE A: {story.get('angle_a')}
ANGLE B: {story.get('angle_b')}
KEY FACTS (from story selector): {json.dumps(story.get('key_facts', []))}

Name the 3 to 5 sharpest questions a reporter must answer before this
debate goes live. Each should be something that, if left vague, would
embarrass the panel on air.

Return JSON only:
{{"questions": ["...", "..."]}}"""

    try:
        plan_raw = await _call_gemini(plan_prompt, temperature=0.3, max_tokens=400)
        questions = parse_json(plan_raw).get("questions") or []
    except Exception as exc:
        log.warning("research plan failed: %s", exc)
        questions = []

    # --- Stage 2: gather related ------------------------------------------
    related = _match_related(questions, related_pool, picked_url=story.get("url", ""))

    # --- Stage 3: synthesis -----------------------------------------------
    synthesis_system = (
        "You are a fast, fastidious research producer. Ground every claim in "
        "either the provided sources or, if you use Google search, the pages "
        "you read. Never invent stats. When unsure, say so explicitly."
    )
    synthesis_prompt = f"""Produce a tight debate briefing.

PRIMARY STORY:
  Headline: {story.get('headline')}
  Source: {story.get('source')}
  URL: {story.get('url', '')}
  Selector key facts: {json.dumps(story.get('key_facts', []))}
  Angles: A="{story.get('angle_a')}" vs B="{story.get('angle_b')}"

QUESTIONS TO ANSWER:
{json.dumps(questions, indent=2) if questions else "(produce your own 3 questions first, then answer them)"}

SIBLING ARTICLES FROM TODAY'S FEED (use freely, cite by index):
{_format_related(related)}

If any fact is still uncertain, use Google search to verify it — but cite the URL.

Return JSON only:
{{
  "questions": ["..."],
  "anchor_facts": [{{"claim": "...", "source": "URL or sibling-index like [2]"}}],
  "counterpoints": ["a position that contradicts angle A with a reason"],
  "pull_quotes": [{{"quote": "short, usable on TV", "attributed_to": "who said it"}}],
  "fresh_data": [{{"stat": "number", "label": "...", "source": "URL"}}],
  "grounded": true
}}"""

    try:
        raw = await _call_gemini(
            synthesis_prompt,
            system=synthesis_system,
            temperature=0.35,
            max_tokens=1400,
            use_grounding=True,
        )
        brief = parse_json(raw)
    except httpx.HTTPStatusError as exc:
        # Tool passthrough sometimes 400s on free Gemini tiers — retry without grounding.
        log.warning("grounded synthesis failed (%s), retrying without grounding", exc)
        raw = await _call_gemini(
            synthesis_prompt,
            system=synthesis_system,
            temperature=0.35,
            max_tokens=1400,
            use_grounding=False,
        )
        brief = parse_json(raw)
        brief["grounded"] = False
    except Exception as exc:
        log.exception("research synthesis failed: %s", exc)
        brief = _mock_briefing(story)

    brief.setdefault("headline", story.get("headline"))
    return brief
