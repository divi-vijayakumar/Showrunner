"""Director — turn one typed scene into one Seedance 2.0 clip.

Prompt shape borrowed from panel_debate's full pipeline:
  STYLE LOCK + SET LOCK + CAST LOCK + THIS SCENE + AUDIO directive +
  SPOKEN LINE — DELIVER VERBATIM (the bit Seedance lip-syncs)

Difference from panel_debate: locked PIXAR aesthetic (not photoreal), Tamil
or English language hint, and the set/cast images come from the user's
uploads (Pixarified) instead of static persona portraits.

Calls generate_seedance_clip with generate_audio=True so the clip carries
its own lip-synced audio. Uses the 4-key BytePlus pool (round-robin per
clip), which makes parallel scene generation safe.
"""
from __future__ import annotations

import logging
from typing import Any

from ....sdk.providers.seedance import generate_seedance_clip

log = logging.getLogger(__name__)


_PIXAR_STYLE_LOCK = (
    "STYLE LOCK — modern Sony Pictures Animation / Pixar / DreamWorks 3D-"
    "animated film aesthetic. NOT a photograph. NOT photorealistic. NOT live "
    "action. Smooth polygonal skin shading, soft cel-shaded look, slightly "
    "oversized expressive eyes, hand-painted texture detail, bold saturated "
    "cinematic color. Every character and every set element MUST stay in this "
    "stylized 3D-animated render — do not drift toward photoreal in any frame."
)


def _language_directive(language: str) -> str:
    if language == "tamil":
        return (
            "LANGUAGE: The spoken line is in TAMIL (தமிழ்). Speak the line "
            "in natural conversational Tamil with native Tamil prosody — "
            "Chennai/Madurai cadence, not academic recitation. Pronounce all "
            "long vowels and retroflex consonants correctly. Do NOT translate "
            "the line to English. Do NOT speak with an English accent."
        )
    return (
        "LANGUAGE: The spoken line is in ENGLISH. Speak it in natural "
        "conversational English with the speaker's appropriate accent for the "
        "story setting. Do not translate."
    )


def _audio_directive(audio_direction: str) -> str:
    """Wraps the writer's audio_direction with strict guards because Seedance's
    output-audio moderator silently drops the audio track on too-emotional
    sounds (baby crying, screaming, shouting, dramatic SFX). We force the
    audio to be MUSIC + clean dialogue ONLY."""
    direction = (audio_direction or "soft solo piano underscore").strip()
    return (
        "AUDIO DIRECTION: " + direction + ". "
        "STRICT AUDIO RULES (Seedance moderator is jumpy):\n"
        "  - The ONLY audio in the clip is: (a) the spoken line below, "
        "(b) the music described above as a low gentle underscore.\n"
        "  - NO sound effects of any kind. NO baby crying, NO screaming, "
        "NO shouting, NO sobbing, NO gasping, NO heavy breathing, NO loud "
        "laughter, NO clapping, NO doorbells, NO phone rings, NO alarms, "
        "NO sirens, NO explosions, NO gunshots, NO breaking glass, NO "
        "dramatic stings, NO action ambient.\n"
        "  - NO background crowd voices. NO chanting. NO real-person names "
        "as utterances.\n"
        "  - Music stays at low underscore volume; the spoken line MUST be "
        "the dominant audio element."
    )


def _spoken_line_block(speaker_label: str, spoken_line: str, language: str) -> str:
    """The verbatim-delivery block. This is the load-bearing chunk Seedance
    actually lip-syncs to (per the prior POC review at
    scripts/scene2_payload_review.md §5)."""
    line = (spoken_line or "").strip()
    if not line:
        return (
            "SPOKEN LINE: (no dialogue this scene — silent visual only, "
            "ambient audio per AUDIO DIRECTION above)."
        )
    speaker = speaker_label if speaker_label and speaker_label != "narrator" else "Off-screen narrator"
    perspective_note = (
        "This is voiceover — there is no on-camera mouth to sync to; "
        "speak the line cleanly over the visual."
        if speaker == "Off-screen narrator"
        else "Lip-sync the on-screen speaker's mouth to these exact words at "
             "a natural conversational pace."
    )
    return (
        "SPOKEN LINE — DELIVER VERBATIM. The featured speaker speaks this "
        "line in full within the clip. DO NOT paraphrase. DO NOT trim. DO NOT "
        "shorten. DO NOT add words. DO NOT summarize. DO NOT translate. "
        f"{perspective_note}\n"
        f"{speaker}: \"{line}\""
    )


def _cast_lock(
    visible_characters: list[str],
    character_descriptions: dict[str, str],
) -> str:
    """One-line-per-character description so Seedance knows who's in frame.

    Cast continuity is the load-bearing constraint here — without it the
    model regenerates the character from scratch each scene and they look
    like different people. Pass the FULL description (no clipping)."""
    if not visible_characters:
        return ""
    lines = []
    for label in visible_characters:
        desc = character_descriptions.get(label, "").strip()
        if desc:
            lines.append(f"  · {label}: {desc}")
        else:
            lines.append(
                f"  · {label}: a stylized 3D-animated character rendered in "
                "modern Pixar key-art style"
            )
    return (
        "CAST IN FRAME — these are the ONLY characters visible in this shot. "
        "Render each character EXACTLY as described below. Same face, same "
        "age, same hair color and length, same skin tone, same wardrobe "
        "(same outfit, accessories, makeup) as in every other scene of this "
        "short film. Cast continuity is the most important constraint — do "
        "NOT drift toward a different age, gender, or look between scenes. "
        "If the first frame already shows the character, match that "
        "appearance verbatim:\n"
        + "\n".join(lines)
    )


def _ambience_block(scene: dict[str, Any]) -> str:
    time_of_day = (scene.get("time_of_day") or "").strip()
    lighting = (scene.get("lighting") or "").strip()
    if not time_of_day and not lighting:
        return ""
    parts = []
    if time_of_day:
        parts.append(f"TIME OF DAY: {time_of_day}.")
    if lighting:
        parts.append(f"LIGHTING: {lighting}.")
    return (
        " ".join(parts)
        + " The clip's exposure, color temperature, and shadows MUST match "
        "this lighting throughout. Do NOT brighten or color-shift away from "
        "the established mood — keep it consistent with the first frame."
    )


def build_scene_prompt(
    scene: dict[str, Any],
    character_descriptions: dict[str, str],
) -> str:
    parts: list[str] = [_PIXAR_STYLE_LOCK]
    ambience = _ambience_block(scene)
    if ambience:
        parts.append(ambience)
    cast = _cast_lock(scene.get("visible_characters", []), character_descriptions)
    if cast:
        parts.append(cast)
    parts.append(
        f"THIS SCENE ({scene.get('title','?')}): "
        + (scene.get("visual_description") or "").strip()
    )
    parts.append(_audio_directive(scene.get("audio_direction", "")))
    parts.append(_language_directive(scene.get("language", "english")))
    parts.append(
        _spoken_line_block(
            scene.get("speaker_label", "narrator"),
            scene.get("spoken_line", ""),
            scene.get("language", "english"),
        )
    )
    return "\n\n".join(parts)


async def render_scene(
    scene: dict[str, Any],
    keyframe_url: str | None,
    next_keyframe_url: str | None,
    character_descriptions: dict[str, str],
    fallback_reference_urls: list[str] | None = None,
) -> str:
    """Generate one Seedance 2.0 clip in showrunner endpoint-locked mode.

    Pattern (matches scripts/test_endpoint_locked_scene2.py + showrunner):
      - first_frame_image = kf_n  (Pixar still generated upstream)
      - last_frame_image  = kf_n+1 (next scene's kf, or None for last scene)
      - Seedance interpolates motion between the two endpoints.

    Cast continuity comes from kf generation upstream (each kf was Seedream-
    edited from the dominant character's portrait + previous kf). Set
    continuity comes from chaining (this clip's last_frame == next clip's
    first_frame).

    `fallback_reference_urls` are used ONLY when no keyframe is available
    (Seedream failed for this scene); in that case we fall back to the old
    multi-reference mode so Seedance still has cast/set context."""
    prompt = build_scene_prompt(scene, character_descriptions)
    duration = int(scene.get("duration_s", 10))
    motion = scene.get("camera_motion", "static")

    log.info(
        "director: scene %d (%s, %ds, lang=%s) — kf=%s next_kf=%s",
        scene.get("scene_number"),
        scene.get("title"),
        duration,
        scene.get("language"),
        "yes" if keyframe_url else "no",
        "yes" if next_keyframe_url else "no",
    )

    if keyframe_url:
        return await generate_seedance_clip(
            prompt=prompt,
            camera_motion=motion,
            duration=duration,
            aspect_ratio="9:16",
            first_frame_image=keyframe_url,
            last_frame_image=next_keyframe_url,
            generate_audio=True,
            provider_override="byteplus",
        )

    # No keyframe for this scene — fall back to multi-reference mode so the
    # clip still gets cast/set context.
    return await generate_seedance_clip(
        prompt=prompt,
        camera_motion=motion,
        duration=duration,
        aspect_ratio="9:16",
        first_frame_image=None,
        reference_images=fallback_reference_urls or [],
        generate_audio=True,
        provider_override="byteplus",
    )
