"""Casting agent — read the brief, define the cast, generate Pixar portraits.

Runs BEFORE the writer so the writer can reference casted character names
in dialogue lines, and the director can pass each character's Pixar portrait
as a Seedance reference_image for cross-scene continuity.

Even when the user uploads photos for some characters, the casting agent
still runs to fill in any characters the user didn't supply (e.g., user
uploads Mom but not Grandma — casting fills Grandma).

Output: a list of cast entries:
    { id, label, age, gender, wardrobe, personality, visual_description,
      portrait_url (Pixar t2i) }

The pipeline merges the casted portraits with any user-uploaded Pixarified
portraits — user uploads always win on conflicting labels.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from ....sdk.providers.llm import call_seed2, parse_json
from .stylize import _ark_seedream  # reuse the t2i helper

log = logging.getLogger(__name__)


_CAST_SYSTEM = """You are a casting director for a short Pixar-style animated film.

Read the story brief and produce a TYPED CAST: every named or implied character
becomes one cast entry with a precise visual description so the same character
looks consistent in every scene.

Strong constraints:
- Pixar key-art aesthetic: warm, expressive, slightly stylized but believable.
- Wardrobe + hair + age + build must be SPECIFIC (not "a young woman" — say
  "early 30s, shoulder-length wavy brown hair tied in a soft bun, oversized
  cream cardigan over a teal sleep shirt, fluffy slippers, tired but loving
  eyes"). Specificity is what makes the portraits reproducible.
- Don't invent characters not implied by the brief.
- Always return STRICT JSON, no prose."""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "cast"


def _user_prompt(beat_sheet: str, existing_labels: list[str]) -> str:
    existing_block = (
        ", ".join(f'"{l}"' for l in existing_labels)
        if existing_labels else "(none)"
    )
    return f"""STORY BRIEF (verbatim from the user):
\"\"\"
{beat_sheet}
\"\"\"

CHARACTERS THE USER ALREADY UPLOADED (do NOT re-cast these — only fill the gaps):
[{existing_block}]

Cast every additional character implied by the brief. For a story like
"Good Night Katya" with a mom trying to put her baby to sleep + grandma + grandpa,
that's typically 3-4 cast members (mom, grandma, grandpa, baby).

Return JSON in EXACTLY this shape:
{{
  "cast": [
    {{
      "label": "<short snake_case_or_lowercase name like 'mom' or 'baby_katya'>",
      "display_name": "<friendly name, like 'Mom' or 'Baby Katya'>",
      "age": "<e.g. 'early 30s' or '7 months'>",
      "gender": "female" | "male" | "child",
      "wardrobe": "<2-3 sentences: clothing, hair, accessories, makeup>",
      "personality": "<1 sentence: how they carry themselves, expressions>",
      "visual_description": "<one rich paragraph combining all the above into a single Pixar-style character description that can be used to generate a portrait AND to describe them in every scene>"
    }}
  ]
}}"""


def _validate_member(m: dict[str, Any]) -> dict[str, Any]:
    label = (m.get("label") or m.get("display_name") or "cast").strip()
    label_slug = _slug(label)
    return {
        "id": label_slug,
        "label": label_slug,
        "display_name": (m.get("display_name") or label).strip(),
        "age": str(m.get("age") or "").strip(),
        "gender": (m.get("gender") or "female").lower(),
        "wardrobe": str(m.get("wardrobe") or "").strip(),
        "personality": str(m.get("personality") or "").strip(),
        "visual_description": str(m.get("visual_description") or "").strip(),
    }


async def cast_for_brief(
    beat_sheet: str,
    existing_character_labels: list[str],
) -> list[dict[str, Any]]:
    """Return cast members the writer should attach to scenes. Each entry
    is a dict with label/visual_description/etc. — `portrait_url` is added
    by `generate_cast_portraits` afterwards."""
    raw = await call_seed2(
        prompt=_user_prompt(beat_sheet, existing_character_labels),
        system=_CAST_SYSTEM,
        temperature=0.55,
        max_tokens=1200,
        force_json=True,
    )
    plan = parse_json(raw)
    cast = plan.get("cast") or []
    if not isinstance(cast, list):
        return []
    out = []
    seen = set()
    for m in cast:
        if not isinstance(m, dict):
            continue
        v = _validate_member(m)
        if v["label"] in seen or v["label"] in existing_character_labels:
            continue
        seen.add(v["label"])
        out.append(v)
    log.info(
        "casting agent produced %d cast members: %s",
        len(out), [c["label"] for c in out],
    )
    return out


def _pixar_portrait_prompt(member: dict[str, Any], extra_safety: bool = False) -> str:
    """Pixar character portrait prompt for a casted member. Heavy on
    fictional-cartoon-mascot keywords so the OutputImage moderator passes.

    `extra_safety=True` adds even louder cartoon negatives — used as a
    fallback when the first pass trips OutputImageSensitiveContentDetected
    (ARK is jumpy on babies, identifiable looks, etc.)."""
    desc = member.get("visual_description") or (
        f"{member.get('age', '')} {member.get('gender', '')}, "
        f"wardrobe: {member.get('wardrobe', '')}, "
        f"personality: {member.get('personality', '')}"
    )
    extra = (
        " EXTRA-CARTOON: exaggerated huge round eyes, big rounded cheeks, "
        "tiny rounded button nose, simple rounded cartoon mouth, simplified "
        "geometric body proportions like a plush toy. Like a Pixar mascot toy."
        if extra_safety else ""
    )
    return (
        "3D-ANIMATED ORIGINAL CARTOON CHARACTER PORTRAIT in the style of a "
        "modern Disney/Pixar/DreamWorks animated film. NOT a photograph. NOT "
        "a likeness of any real person. NOT a celebrity. NOT a public figure. "
        "Heavily stylized with smooth polygonal skin, oversized expressive "
        "eyes (clearly non-human cartoon proportions), soft cel-shaded look, "
        "hand-painted texture detail, warm cinematic color palette. Looks "
        f"unmistakably like a cartoon mascot, not photoreal.{extra} "
        f"CHARACTER ({member.get('display_name', member.get('label'))}): {desc}. "
        "Head-and-shoulders framing, gentle composed expression, eye-line to "
        "camera, clean simple soft-lit background, 9:16 vertical composition. "
        "No on-screen text or logos."
    )


async def generate_cast_portrait(member: dict[str, Any]) -> str | None:
    """Generate one Pixar portrait via ARK Seedream t2i. Two-pass:
    first the natural prompt, on OutputImageSensitiveContentDetected retry
    with extra-cartoon negatives. Returns URL or None on terminal failure."""
    label = member.get("label", "?")
    try:
        return await _ark_seedream(_pixar_portrait_prompt(member))
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        if "OutputImageSensitiveContent" in body or "InputImageSensitiveContent" in body:
            log.warning(
                "cast portrait %r flagged sensitive — retrying with extra-cartoon prompt",
                label,
            )
            try:
                return await _ark_seedream(
                    _pixar_portrait_prompt(member, extra_safety=True)
                )
            except Exception as exc2:
                log.warning(
                    "cast portrait %r still failed after retry: %s",
                    label, str(exc2)[:200],
                )
                return None
        log.warning(
            "cast portrait gen failed for %r: %s",
            label, str(exc)[:200],
        )
        return None
    except Exception as exc:
        log.warning(
            "cast portrait gen failed for %r: %s",
            label, str(exc)[:200],
        )
        return None


async def generate_cast_portraits(
    cast: list[dict[str, Any]],
) -> dict[str, str]:
    """Generate portraits for the whole cast in parallel.
    Returns {label: ark_cdn_url}."""
    if not cast:
        return {}
    results = await asyncio.gather(
        *[generate_cast_portrait(m) for m in cast],
        return_exceptions=False,
    )
    return {
        m["label"]: url
        for m, url in zip(cast, results)
        if url
    }
