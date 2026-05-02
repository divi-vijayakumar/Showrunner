"""Per-scene keyframes for the showrunner endpoint-locked pipeline.

For each scene n, we want a still image that captures its opening composition
(kf_n). Seedance then animates from kf_n → kf_(n+1) using `first_frame +
last_frame` mode. This gives:

  - Cast continuity: every keyframe is generated via Seedream `image` (i2i)
    with cast portraits + (optionally) the previous keyframe as visual
    references — same character looks the same across scenes.
  - Set continuity: the previous keyframe carries set lighting + decor
    forward, so cuts feel like coverage of one location, not different
    renders.
  - Scene staging: each keyframe is its own deliberate composition, prompted
    from the writer's visual_description.

ARK Seedream `/images/generations` accepts a single `image` field for i2i.
We approximate "multiple references" by chaining: the first reference (cast
portrait of the dominant character) is the i2i seed, and the rest of the
references are described in the prompt by URL-naming. Practically the seed
image is the strongest signal, so we pick it carefully:
  - If the scene has a speaker_label in our cast → that character's portrait
  - Else if the scene has any visible_characters → first one with a portrait
  - Else if there's a previous keyframe → chain forward
  - Else → t2i (no seed)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

from .stylize import _ark_seedream

log = logging.getLogger(__name__)

_KF_CACHE_DIR = "/tmp/tabloid_drama_keyframes"


def _cache_path(scene_id: str, prompt_hash: str, seed_hash: str) -> str:
    os.makedirs(_KF_CACHE_DIR, exist_ok=True)
    return os.path.join(_KF_CACHE_DIR, f"{scene_id}_{prompt_hash}_{seed_hash}.url.txt")


def _keyframe_prompt(
    scene: dict[str, Any],
    cast_descriptions: dict[str, str],
    visible: list[str],
) -> str:
    """Compose the Seedream prompt for one scene's keyframe.

    Critical: time_of_day + lighting come FIRST in the prompt because
    Seedream defaults to bright daylight without an explicit night cue.
    Without these the model will render a noon-bright bedroom even when
    the brief says "midnight"."""
    visual = (scene.get("visual_description") or "").strip()
    time_of_day = (scene.get("time_of_day") or "").strip()
    lighting = (scene.get("lighting") or "").strip()
    cast_lines = []
    for label in visible:
        desc = cast_descriptions.get(label, "")
        if desc:
            cast_lines.append(f"  · {label}: {desc[:300]}")
        else:
            cast_lines.append(f"  · {label}")
    cast_block = (
        "CHARACTERS IN FRAME (render each consistently with the supplied "
        "reference portrait — same face, hair, age, wardrobe in every frame):\n"
        + "\n".join(cast_lines)
    ) if cast_lines else ""
    ambience_block = ""
    if time_of_day or lighting:
        parts = []
        if time_of_day:
            parts.append(f"TIME OF DAY: {time_of_day}.")
        if lighting:
            parts.append(f"LIGHTING: {lighting}.")
        ambience_block = (
            " ".join(parts)
            + " The frame MUST visually match this — if the time is night, "
            "the room is mostly in shadow with only the named light sources "
            "lit. Do NOT render a bright daytime scene unless the time of "
            "day explicitly says so."
        )
    return (
        "3D-ANIMATED FILM STILL FRAME in the style of modern Disney/Pixar/"
        "DreamWorks animated film. NOT a photograph. NOT photorealistic. "
        "Smooth polygonal skin shading, soft cel-shaded look, oversized "
        "expressive eyes, hand-painted texture detail, bold saturated "
        "cinematic color. Shot looks like one frame pulled from a finished "
        "animated film.\n\n"
        f"{ambience_block}\n\n"
        f"{cast_block}\n\n"
        f"SCENE: {visual}\n\n"
        "FRAMING: 9:16 vertical composition. Cinematic depth of field. "
        "Background and foreground both rendered in the same animation "
        "style — no photoreal elements. No on-screen text or logos."
    )


async def generate_one_keyframe(
    scene: dict[str, Any],
    cast_descriptions: dict[str, str],
    cast_portraits: dict[str, str],
    prev_keyframe_url: str | None,
) -> str | None:
    """Generate one scene's keyframe. Returns the ARK CDN URL or None on
    failure. Strategy: Seedream i2i with the dominant character's portrait
    as the seed image (or the previous keyframe when no characters in
    frame), prompted with the scene's visual_description."""
    visible = list(scene.get("visible_characters") or [])
    speaker = scene.get("speaker_label")
    if speaker and speaker != "narrator" and speaker not in visible:
        visible.insert(0, speaker)

    # Pick the seed image: dominant character > prev keyframe > t2i.
    seed_url: str | None = None
    seed_label = ""
    for label in visible:
        if label in cast_portraits:
            seed_url = cast_portraits[label]
            seed_label = label
            break
    if not seed_url and prev_keyframe_url:
        seed_url = prev_keyframe_url
        seed_label = "prev_keyframe"

    prompt = _keyframe_prompt(scene, cast_descriptions, visible)
    sc_id = f"sc{scene.get('scene_number', 0):02d}"
    h_prompt = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]
    h_seed = hashlib.sha1((seed_url or "t2i").encode("utf-8")).hexdigest()[:6]
    cache_path = _cache_path(sc_id, h_prompt, h_seed)
    if os.path.exists(cache_path):
        cached = open(cache_path).read().strip()
        if cached.startswith("http"):
            log.info("keyframe cache hit for %s", sc_id)
            return cached

    try:
        if seed_url:
            log.info(
                "keyframe %s: i2i seeded by %s",
                sc_id, seed_label,
            )
            img_url = await _ark_seedream(prompt, image=seed_url)
        else:
            log.info("keyframe %s: t2i (no seed)", sc_id)
            img_url = await _ark_seedream(prompt)
    except Exception as exc:
        log.warning(
            "keyframe %s failed: %s",
            sc_id, str(exc)[:200],
        )
        return None

    with open(cache_path, "w") as f:
        f.write(img_url)
    return img_url


async def generate_scene_keyframes(
    scenes: list[dict[str, Any]],
    cast_descriptions: dict[str, str],
    cast_portraits: dict[str, str],
) -> list[str | None]:
    """Generate one keyframe per scene IN ORDER (sequential, not parallel —
    each keyframe references the previous one for continuity).

    Returns a list of len(scenes), entries are ARK CDN URLs or None.
    The first scene's keyframe has no `prev_keyframe_url` to seed from,
    so it falls back to a character portrait or t2i.
    """
    out: list[str | None] = []
    prev: str | None = None
    for sc in scenes:
        kf = await generate_one_keyframe(sc, cast_descriptions, cast_portraits, prev)
        out.append(kf)
        if kf:
            prev = kf
    return out
