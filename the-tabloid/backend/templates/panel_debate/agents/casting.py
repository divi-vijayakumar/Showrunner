"""Casting agent — generates 3 story-relevant guests on the fly.

Replaces the static personas-per-channel pool. Anchors stay channel-level
(see backend/anchors.py); guests are cast fresh for every story so the
panel actually matches what's being debated.

For example: an AAP-defection-in-Delhi story should NOT be debated by
Tamil-Nadu personas. The casting agent reads the story brief and produces
three guests whose region, lean, expertise, and visual look fit the story.

Single Gemini call (or whatever LLM_PROVIDER is configured), structured
JSON output, falls back to the static pool on failure so the pipeline
always has a panel.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ....sdk.providers.llm import call_seed2, parse_json

log = logging.getLogger(__name__)


# Roles a panel always covers. The anchor is provided externally (channel-
# level), so the casting agent fills these three.
GUEST_ROLES = ("provocateur", "analyst", "humanist")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "guest"


def _validate(guest: dict[str, Any]) -> dict[str, Any]:
    """Coerce + fill in safe defaults so downstream code never crashes on
    a malformed LLM response."""
    name = (guest.get("name") or "Guest").strip()
    role = (guest.get("role") or "").strip().lower()
    if role not in GUEST_ROLES:
        role = "humanist"  # safest default — the show always has one
    voice = guest.get("voice") or {}
    if not isinstance(voice, dict):
        voice = {}
    return {
        "id": (guest.get("id") or _slug(name)),
        "name": name,
        "role": role,
        "lean": str(guest.get("lean") or ""),
        "culture": str(guest.get("culture") or ""),
        "style": str(guest.get("style") or ""),
        "voice": {
            "gender": str(voice.get("gender", "female")).lower(),
            "pace": float(voice.get("pace", 1.0)),
            "warmth": str(voice.get("warmth", "medium")),
        },
        "visual_description": str(guest.get("visual_description") or ""),
    }


async def cast_guests(
    *,
    story: dict[str, Any],
    anchor: dict[str, Any],
    channel_id: str,
) -> list[dict[str, Any]]:
    """Return 3 guests (provocateur, analyst, humanist) cast for this story.

    `anchor` is the channel host as a persona dict (from anchors.as_persona()).
    The agent is told who the anchor is so it doesn't clone them.

    On any failure, returns an empty list — caller should fall back.
    """
    angles = (
        f"  Angle A: {story.get('angle_a','')}\n"
        f"  Angle B: {story.get('angle_b','')}"
    ) if (story.get("angle_a") or story.get("angle_b")) else ""

    facts_block = ""
    facts = story.get("key_facts") or []
    if facts:
        facts_block = "Key facts:\n" + "\n".join(f"  - {f}" for f in facts[:6])

    prompt = f"""You are the casting director for a news debate channel: {channel_id.replace('_', ' ')}.

Tonight's story:
  Headline: {story.get('headline','')}
  Source: {story.get('source','')}
  Why now: {story.get('why_now','')}
{angles}
{facts_block}

The HOST is already chosen — do NOT cast someone too similar:
  Name: {anchor.get('name','')}
  Lean/posture: {anchor.get('lean','')}
  Culture: {anchor.get('culture','')}

Cast THREE guests for the panel. Roles are fixed; each must be filled with
a real-feeling person whose region, profession, lean, and lived experience
match THIS story's stakeholders. Region-match the story, NOT the host:

  - A Delhi or Punjab story → Delhi/Punjab voices.
  - A Tamil Nadu story → Tamil Nadu voices.
  - A Berlin or Brussels EU policy story → German / EU-policy voices.
  - A Lagos or Nairobi tech story → Nigerian / Kenyan voices.
  - A Tokyo or Seoul finance story → Japanese / Korean voices.
  - A Bay Area or NYC tech story → US tech voices.
  - An AI lab story → people inside that world (lab researchers,
    open-source maintainers, ML safety folk).
  - A Hollywood / Bollywood / K-pop story → industry-native voices for
    the country of origin.

Do NOT default to Indian guests when the story is not India-rooted.
Do NOT default to American guests when the story is not US-rooted.
Pick the cultural register the actual stakeholders inhabit. Avoid
generic "international observer" types unless the story is genuinely
multi-region.

Names, ethnicity in visual_description, and cultural references in
`culture`/`style` should all match the region. The host's culture is
listed above purely so you don't clone them — it does NOT constrain
the guests' region.

Cover all three roles, no duplicates:
  - provocateur: takes the most controversial defensible position; sharp,
    short sentences, makes the others uncomfortable.
  - analyst: data + historical precedent + expert consensus; counterweight
    voice, long view.
  - humanist: brings it back to one real person this story affects;
    emotional but specific, never abstract.

Return JSON only — no prose, no code fences. Fill in EVERY field for
every guest. The shape is:
{{
  "guests": [
    {{
      "id": "first_last_lowercase",
      "name": "First Last",
      "role": "provocateur",
      "lean": "one-line political/professional posture",
      "culture": "region + class + occupational context",
      "style": "how they argue: sharp / measured / anecdotal / data-first / etc.",
      "voice": {{"gender": "male" | "female" | "nonbinary",
                 "pace": 0.85 to 1.20,
                 "warmth": "low" | "medium" | "high"}},
      "visual_description": "ethnicity + age range + hair + glasses + clothing color and texture + background hint + lighting direction + expression"
    }},
    {{ "id": "...", "role": "analyst", ... }},
    {{ "id": "...", "role": "humanist", ... }}
  ]
}}

Visual_description rules:
- Specific: ethnicity, age range, hair, clothing color/texture, background,
  lighting direction, expression. Repeat-able verbatim across scenes so the
  video model holds character consistency.
- No on-screen text. No logos. No camera names. No real-celebrity references.
- 9:16 broadcast framing implied; medium close-up, eye-line locked to camera.

Voice rules:
- gender: "male" | "female" | "nonbinary"
- pace: 0.85 (slow) to 1.20 (fast)
- warmth: "low" | "medium" | "high"
"""

    try:
        raw = await call_seed2(
            prompt,
            temperature=0.7,
            max_tokens=2000,
            force_json=True,
        )
        data = parse_json(raw)
    except Exception as exc:
        log.warning("casting agent failed: %s — caller should fall back", exc)
        return []

    raw_guests = data.get("guests")
    if not isinstance(raw_guests, list):
        log.warning("casting agent returned no guests array; got %r", list(data)[:5])
        return []

    cleaned: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for g in raw_guests:
        if not isinstance(g, dict):
            continue
        v = _validate(g)
        # First-of-each-role wins; drop dupes silently rather than fail.
        if v["role"] in seen_roles:
            continue
        seen_roles.add(v["role"])
        cleaned.append(v)

    # If a role is missing, the pipeline can still run — but log it so we
    # know the agent left a hole.
    missing = set(GUEST_ROLES) - seen_roles
    if missing:
        log.warning("casting: missing roles %s — pipeline will degrade", missing)

    return cleaned


def panel_for(
    *,
    anchor_persona: dict[str, Any],
    guests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Final 4-persona panel: anchor + 3 guests. Order doesn't matter for
    debate_engine (it maps by role) but anchor first reads naturally."""
    return [anchor_persona, *guests]


# Optional helper used by tests/dev:
def to_json(panel: list[dict[str, Any]]) -> str:
    return json.dumps(panel, indent=2)
