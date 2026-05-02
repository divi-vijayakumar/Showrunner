"""Writer agent — turn a free-form beat sheet into a typed scene plan.

Input: beat sheet text (the user's loose scene-by-scene description) +
list of {label} for each uploaded character/set asset + a default language.

Output: a list of scenes with:
    scene_number, title, duration_s (5/10/15), language ('tamil'|'english'),
    spoken_line (the verbatim line in the chosen language),
    speaker_label (matches an uploaded character label, or 'narrator'),
    set_label (matches an uploaded set label, or '__generate__'),
    visible_characters (list of character labels in frame),
    camera_motion (dolly_in / dolly_out / static),
    visual_description (one sentence for the director),
    audio_direction (one sentence — what music/SFX/ambient besides the spoken line)

Plus a list of `sets_to_generate` for any set described in the beat sheet
that the user did NOT upload — the set_designer will handle those.

Designed to be tolerant of messy input. The Vijay sample brief uses Tamizh
phonetic transliteration, parenthetical asides, ages, etc. The LLM normalizes
all of that into structured scenes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ....sdk.providers.llm import call_seed2, parse_json

log = logging.getLogger(__name__)


_SYSTEM = """You are a SCREENWRITER for a short PIXAR-style animated film.

Your job: turn a loose beat sheet into a typed scene plan. Each scene becomes
ONE Seedance 2.0 clip (5, 10, or 15 seconds) with native lip-synced audio.

CREATIVE BAR:
- Pixar tone: warm, gentle humor, character-driven, emotional arc with payoff.
- Show, don't tell. Use IN-WORLD DIALOGUE (characters speaking to each other),
  NOT off-screen narration. Narration is reserved for the rare bookend or
  when the scene literally has no on-screen character.
- Every line should reveal character. A grandma trying a lullaby reveals
  patience; a baby giggling reveals mischief. Don't write filler.
- Visual storytelling matters as much as dialogue. Some scenes can be silent
  with just music — say so in the audio_direction.

OUTRO / TITLE BEAT:
- The FINAL scene of every film MUST be the title beat. Render the title
  in-world: e.g. "the family is asleep, soft golden light, the title 'GOOD
  NIGHT KATYA' appears in soft hand-painted letters above them, then holds."
  Seedance handles the title animation natively — DO NOT delegate the title
  to an external editor pass.
- That final scene's `visible_characters` can be empty (camera pulls back
  past the cast) or include them all. `audio_direction` is just a soft
  music sting (e.g. "soft solo piano outro, no dialogue").
- `outro_card` in the response is metadata-only (UI uses it to display the
  title near the player) — the editor will NOT synthesize a separate card.

AMBIENCE (load-bearing — Seedream renders what you describe):
- Every scene MUST set `time_of_day` and `lighting` precisely. The image
  model renders bright noon by default — without an explicit night cue
  it WILL render bright. Be loud about darkness when the scene is at night.
- time_of_day examples: "11pm", "midnight", "2am pre-dawn", "golden hour",
  "rainy afternoon".
- lighting examples: "single warm bedside lamp, deep blue shadows, mostly
  dark room", "soft golden pre-dawn light through window, warm amber on
  faces", "moonlight through curtains, cool teal shadows".
- visual_description should reuse those cues (e.g. "Mom rocks Katya, only
  the bedside lamp lit, room is mostly in deep blue shadow").

CONTINUITY:
- Use character labels EXACTLY as supplied in the cast list. The same
  character must appear under the same label in every scene they're in.
- Match scenes to characters: if "mom" is putting "baby_katya" to sleep,
  put both in visible_characters.
- speaker_label MUST be one of the cast labels (or "narrator" only if
  truly off-screen voiceover, which should be rare).

LANGUAGE:
- Each scene picks ONE language for its spoken_line. Use the default
  language unless the brief explicitly calls for code-switching.
- For Tamil scenes, write the line in Tamil script (தமிழ்).

AUDIO (very strict — Seedance's output-audio moderator is jumpy):
- audio_direction must ONLY describe ambient music. Use this strict allowlist:
  "soft solo piano", "warm acoustic guitar", "gentle music box lullaby",
  "soft strings underscore", "warm cello underscore", "soft jazz brushes",
  "gentle wind chimes", "room tone".
- NEVER include in audio_direction: baby cries, crying sounds, screaming,
  shouting, yelling, gasping, sobbing, grunting, sighing heavily, wailing,
  political language, news, real-person names, weapons, sirens, alarms,
  explosions, breaking glass, dramatic stings, action SFX. These TRIP the
  Seedance moderator and silently drop the scene's audio.
- audio_direction can describe a feeling: "warm and reassuring", "playful
  and curious", "tender and sleepy". Avoid sound-effect verbs (cry, shout,
  laugh-loudly, etc.) — keep it musical and atmospheric.
- Spoken lines (dialogue) are fine in any tone, but keep them gentle and
  short (1-2 sentences max per scene).

Always return STRICT JSON. No prose around it. No markdown fences."""


def _build_user_prompt(
    beat_sheet: str,
    character_labels: list[str],
    set_labels: list[str],
    default_language: str,
    cast_descriptions: dict[str, str] | None = None,
) -> str:
    cast_descriptions = cast_descriptions or {}
    if cast_descriptions:
        chars_block = "\n".join(
            f'  - "{lbl}": {desc}' for lbl, desc in cast_descriptions.items()
        )
    else:
        chars_block = (
            "  " + (", ".join(f'"{c}"' for c in character_labels) or "(none)")
        )
    sets_block = ", ".join(f'"{s}"' for s in set_labels) or "(none uploaded)"
    return f"""BEAT SHEET (verbatim from the user):
\"\"\"
{beat_sheet}
\"\"\"

CAST (use these EXACT labels as speaker_label / visible_characters entries —
each label maps to a Pixar character portrait that travels through every scene):
{chars_block}

UPLOADED SET LABELS (use these EXACT strings as set_label):
[{sets_block}]

DEFAULT LANGUAGE: {default_language} (use this when the beat sheet doesn't specify)

Produce a scene plan suitable for a 90-180 second short film. Aim for 6-10 scenes,
each 5/10/15 seconds. Pick durations that match the line length — a 4-word line
is 5s, a full sentence is 10s, a long monologue or montage beat is 15s.

For each scene:
- "spoken_line" must be the exact text the on-screen character (or off-screen
  narrator) speaks. If the language is Tamil, write the line in Tamil script
  (Unicode தமிழ்). If the user wrote it in romanized Tamil ("vijay enum nan"),
  convert to Tamil script.
- "speaker_label" must EXACTLY match one of the uploaded character labels,
  or the literal string "narrator" if the line is voiceover with no on-screen
  speaker.
- "set_label" must EXACTLY match one of the uploaded set labels OR the
  literal string "__generate__" if you need a set that wasn't uploaded
  (in which case add an entry to "sets_to_generate" with a description).
- "camera_motion" must be one of: "dolly_in", "dolly_out", "static".
- "visual_description" is one sentence telling the video model what to render
  (composition, action, mood). Do NOT repeat the spoken line here.
- "audio_direction" is one sentence describing background ambient/music/SFX
  the video should include alongside the spoken line. If only dialogue +
  room tone, say "studio dialogue only". If a crowd is cheering, name what
  they're chanting. Keep it tame — no gunshots/explosions/dramatic stings,
  the moderator rejects those.

Return JSON in EXACTLY this shape:
{{
  "title": "<short title for the film>",
  "default_language": "{default_language}",
  "sets_to_generate": [
    {{"id": "<short_snake_case_id>", "description": "<2-3 sentence visual description for Seedream t2i>"}}
  ],
  "scenes": [
    {{
      "scene_number": 1,
      "title": "<3-5 word scene title>",
      "duration_s": 5 | 10 | 15,
      "language": "tamil" | "english",
      "spoken_line": "<exact text>",
      "speaker_label": "<character label or 'narrator'>",
      "set_label": "<set label or '__generate__' or generated set id>",
      "visible_characters": ["<character labels in frame>"],
      "camera_motion": "dolly_in" | "dolly_out" | "static",
      "time_of_day": "<e.g. '11pm', 'midnight', '2am pre-dawn'>",
      "lighting": "<e.g. 'single warm bedside lamp, deep blue shadows, mostly dark room'>",
      "visual_description": "<one sentence — REUSE the time_of_day + lighting cues here>",
      "audio_direction": "<one sentence>"
    }}
  ],
  "outro_card": {{
    "text": "<final on-screen text, e.g. 'CM 2026'>",
    "duration_s": 3
  }}
}}"""


async def scene_plan_from_brief(
    beat_sheet: str,
    character_labels: list[str],
    set_labels: list[str],
    default_language: str = "tamil",
    cast_descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the writer agent. Raises if the LLM returns malformed JSON; the
    pipeline catches and surfaces the error to the segment status doc.

    `cast_descriptions` is a {label: visual_description} map produced by the
    casting agent. When supplied, the writer references them by label so
    dialogue is grounded in actual cast members instead of generic
    'narrator' fallbacks."""
    user_prompt = _build_user_prompt(
        beat_sheet, character_labels, set_labels, default_language,
        cast_descriptions=cast_descriptions,
    )
    raw = await call_seed2(
        prompt=user_prompt,
        system=_SYSTEM,
        # Higher temperature — Pixar tone needs more creative latitude than
        # the news debate writer that seeded this template.
        temperature=0.85,
        # 4500 because adding time_of_day + lighting per scene pushed the
        # 6-scene plan past the old 2400 ceiling, truncating the JSON.
        max_tokens=4500,
        force_json=True,
    )
    plan = parse_json(raw)
    _validate(plan, character_labels, set_labels)
    log.info(
        "writer: produced %d scenes (default lang=%s, sets_to_generate=%d)",
        len(plan.get("scenes", [])),
        plan.get("default_language"),
        len(plan.get("sets_to_generate", [])),
    )
    return plan


def _validate(
    plan: dict[str, Any], character_labels: list[str], set_labels: list[str]
) -> None:
    """Tighten LLM slop. Coerce duration to {5,10,15}, normalize camera_motion,
    fall back to default language on missing per-scene language. Doesn't fix
    structural problems — those raise so the user sees it on the UI."""
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("writer: scene plan has no 'scenes' array")

    valid_motions = {"dolly_in", "dolly_out", "static"}
    valid_durations = {5, 10, 15}
    set_set = set(set_labels) | {"__generate__"}
    generated_ids = {s.get("id") for s in plan.get("sets_to_generate", []) if s.get("id")}
    set_set |= generated_ids

    for i, sc in enumerate(scenes):
        sc["scene_number"] = i + 1
        d = sc.get("duration_s") or 10
        sc["duration_s"] = min(valid_durations, key=lambda v: abs(v - int(d)))
        m = sc.get("camera_motion") or "static"
        if m not in valid_motions:
            sc["camera_motion"] = "static"
        lang = (sc.get("language") or plan.get("default_language") or "english").lower()
        sc["language"] = "tamil" if "tamil" in lang or "தமிழ்" in lang else "english"
        if not sc.get("spoken_line"):
            sc["spoken_line"] = ""
        # NOTE: writer is allowed to invent character labels — Script runs
        # BEFORE Casting in the new pipeline order, so Casting will see
        # whatever labels the writer uses and generate portraits for them.
        # Don't coerce to 'narrator' here.
        sp = (sc.get("speaker_label") or "").strip() or "narrator"
        sc["speaker_label"] = _slug_label(sp)
        st = sc.get("set_label") or "__generate__"
        if st not in set_set:
            # Set label not uploaded and not in writer's sets_to_generate.
            # Add it to sets_to_generate with a description derived from the
            # scene's visual_description so the set designer can render it.
            label_slug = _slug_label(st)
            new_set = {
                "id": label_slug,
                "description": (sc.get("visual_description") or st).strip(),
            }
            plan.setdefault("sets_to_generate", []).append(new_set)
            set_set.add(label_slug)
            sc["set_label"] = label_slug
            log.info(
                "writer: scene %d set_label %r added to sets_to_generate",
                i + 1, label_slug,
            )
        else:
            sc["set_label"] = st
        if not isinstance(sc.get("visible_characters"), list):
            sc["visible_characters"] = []
        # Slug visible character labels too for consistency.
        sc["visible_characters"] = [
            _slug_label(v) for v in sc["visible_characters"] if v
        ]


def _slug_label(s: str) -> str:
    """Lowercase + underscore — match the casting agent's label slugging
    so 'Mom' from the writer matches 'mom' from the casting agent."""
    import re as _re
    out = _re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return out or "narrator"
