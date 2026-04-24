"""BytePlus Seed 2.0 client + JSON parsing helpers.

Kept deliberately thin — one `call_seed2` function used by all agents.
When `TABLOID_MOCK=1`, returns canned content so the pipeline runs offline.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response, being forgiving about
    code fences and chatter before/after."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


async def call_seed2(
    prompt: str,
    system: str | None = None,
    *,
    temperature: float = 0.8,
    max_tokens: int = 800,
    force_json: bool | None = None,
) -> str:
    """Call BytePlus ARK Seed. Returns the raw assistant text.

    `force_json=True` adds response_format=json_object, which Seed respects —
    the prompt asking for "JSON only" isn't enough on its own. When not
    explicitly set, we auto-enable JSON mode if the prompt says "Return JSON".
    """
    if settings().mock:
        return _mock_response(prompt, system)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    if force_json is None:
        force_json = "return json" in prompt.lower()

    payload: dict[str, Any] = {
        "model": settings().seed_llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings().seed_llm_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("Seed error %s at %s: %s", resp.status_code, resp.url, resp.text[:300])
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


# -- Mock canned responses --------------------------------------------------
# Keep these short; they only need to satisfy the JSON shape expected by callers.


_MOCK_STORY = {
    "headline": "Mock headline: AI town halls reshape local governance",
    "source": "The Tabloid Mock Wire",
    "key_facts": [
        "12 cities piloted AI-moderated town halls this quarter",
        "Turnout rose 38% on average versus traditional sessions",
        "Critics cite transparency gaps in how agendas are set",
    ],
    "angle_a": "AI-moderated sessions are more inclusive and efficient",
    "angle_b": "Delegating civic moderation to AI erodes accountability",
    "why_now": "Three state legislatures are drafting rules this month",
    "infographic_data": {
        "key_stat": "38%",
        "stat_source": "Municipal Engagement Index 2026",
        "context": "Average turnout lift across 12 pilot cities",
    },
}


def _mock_response(prompt: str, system: str | None) -> str:
    """Cheap pattern-matching to satisfy whatever caller is asking."""
    p = (prompt or "").lower()
    s = (system or "").lower()

    if "select the one story" in p or "story editor" in p:
        return json.dumps(_MOCK_STORY)

    if "director of the tabloid" in p or "convert this news debate" in p:
        # Seven scenes matching the cinematic spine in script_compiler.SCENE_SPINE.
        seven = [
            ("Cold Open — Podium Sweep", "anchor", "crane_up", "WIDE_GROUP", "anticipation",
             "Welcome to The Tabloid. I'm the anchor. Tonight — a story that refuses to stay quiet."),
            ("Anchor Intro", "anchor", "dolly_in", "MEDIUM_SOLO", "grounded",
             "Here's what we know, and here's what's being argued."),
            ("Provocateur Take", "provocateur", "pan_right", "CLOSE_SOLO", "fire",
             "If we pretend this is complicated, we're just protecting the people who caused it."),
            ("Analyst Counter", "analyst", "orbit_right", "MEDIUM_SOLO", "measured",
             "The numbers don't back that up. The pattern is the opposite of what you're claiming."),
            ("Humanist Moment", "humanist", "dolly_in", "CLOSE_SOLO", "tender",
             "I keep thinking about the person on the other end of this policy."),
            ("Clash", "cross", "whip_pan", "FAST_CROSS_CUT", "heat",
             "Four voices, one story, no more hiding behind talking points."),
            ("Anchor Close", "anchor", "dolly_out", "WIDE_GROUP", "resolve",
             "That's The Tabloid."),
        ]
        scenes = []
        for i, (title, role, cam, shot, beat, vo) in enumerate(seven):
            scenes.append(
                {
                    "scene_number": i + 1,
                    "title": title,
                    "featured_role": role,
                    "featured_persona_id": None,  # filled in by _validate
                    "duration": 5,
                    "camera_motion": cam,
                    "shot": shot,
                    "emotional_beat": beat,
                    "seedance_prompt": (
                        f"Mock scene {i+1}: {title}. Broadcast set with four panelists, "
                        f"camera {cam}, mood {beat}."
                    ),
                    "vo_line": vo,
                }
            )
        return json.dumps(
            {
                "scenes": scenes,
                "infographics": [
                    {
                        "type": "stat_card",
                        "scene_index": 3,
                        "timestamp_in_scene": 1.5,
                        "duration": 3.0,
                        "data": {"number": "38%", "label": "Turnout lift", "stat_source": "Mock Research"},
                    },
                    {
                        "type": "quote_pull",
                        "scene_index": 2,
                        "timestamp_in_scene": 1.0,
                        "duration": 3.5,
                        "data": {
                            "quote": "This is not a technology story, it's a power story.",
                            "speaker": "Analyst",
                        },
                    },
                ],
                "vo_script": [
                    {
                        "persona_name": s["featured_role"],
                        "agent": s["featured_role"],
                        "line": s["vo_line"],
                        "scene_index": i,
                    }
                    for i, s in enumerate(scenes)
                ],
            }
        )

    # Default: a single debate line in character
    role = "panelist"
    if "provocateur" in s:
        role = "provocateur"
    elif "analyst" in s:
        role = "analyst"
    elif "humanist" in s:
        role = "humanist"
    elif "anchor" in s:
        role = "anchor"

    lines = {
        "anchor": "Let's cut through the noise. Here's the story everyone's going to be arguing about by morning.",
        "provocateur": "Oh please. If we pretend this is complicated we're just protecting the people who caused it.",
        "analyst": "The numbers don't back that up. Look at the last three cycles — the pattern is the opposite.",
        "humanist": "I keep thinking about the person on the other end of this policy. That's who's actually paying.",
        "panelist": "Fair point, but we should slow down before we declare this settled.",
    }
    return lines[role]
