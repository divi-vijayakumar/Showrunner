"""Compile the finished debate into a cinematic broadcast script.

Produces a 7-scene arc shaped like The Daily Show — locked stage, calm
camera, energy comes from the host's face and the writing, not kinetic
camera tricks.

  1. COLD OPEN — pull from the anchor MCU to the locked 4-panel wide.
  2. ANCHOR INTRO — slow push back into the anchor; states the story.
  3. PROVOCATEUR TAKE — held MCU, the line lands without camera help.
  4. ANALYST COUNTER — subtle push as the data drops.
  5. HUMANIST MOMENT — held, let the human moment breathe.
  6. CLASH — held; the conflict is in the cut + dialogue, not the camera.
  7. ANCHOR CLOSE — pull back to the locked wide. "That's The Tabloid."

Camera grammar is constrained to 3 motions only — `dolly_in`, `dolly_out`,
`static`. Pan/orbit/tilt/crane/whip are dropped because they re-stage the
locked set or fight the calm tone.

Each scene names the persona it features via `persona_id` so the video layer
can pick the right MCU derivative (or chain from the previous clip's last
frame when the same persona speaks back-to-back) into Seedance.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import channel_or_raise
from ..sdk.providers.llm import call_seed2, parse_json


# Deterministic spine for the script. The LLM fills in the dialogue and visual
# beats, but the camera grammar, featured role, and duration stay fixed so the
# demo always lands.
# 24-scene spine — 2 minutes total at 5s per Seedance clip. Story arc:
#   1-3   Anchor cold open + frames the story
#   4-6   Provocateur first take (open / develop / land)
#   7-9   Analyst counter (counter / data drop / close)
#   10-12 Humanist reframes the human side
#   13-15 Round 2 — quick exchanges between the three guests
#   16-18 Anchor pivots to clash + cross-cut clash beats
#   19-21 Each guest's final position (one beat each)
#   22-24 Anchor reflection + close on locked wide
SCENE_SPINE: list[dict[str, Any]] = [
    {"scene_number": 1,  "title": "Cold Open — Wide Establish",      "featured_role": "anchor",      "duration": 5, "camera_motion": "dolly_in",  "shot": "WIDE_GROUP"},
    {"scene_number": 2,  "title": "Anchor Names Tonight's Story",    "featured_role": "anchor",      "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 3,  "title": "Anchor Sets the Stakes",          "featured_role": "anchor",      "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 4,  "title": "Provocateur Opens",               "featured_role": "provocateur", "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 5,  "title": "Provocateur Develops",            "featured_role": "provocateur", "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 6,  "title": "Provocateur Lands the Punch",     "featured_role": "provocateur", "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 7,  "title": "Analyst Counter",                 "featured_role": "analyst",     "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 8,  "title": "Analyst Drops the Data",          "featured_role": "analyst",     "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 9,  "title": "Analyst Closes the Argument",     "featured_role": "analyst",     "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 10, "title": "Humanist Reframes",               "featured_role": "humanist",    "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 11, "title": "Humanist Holds the Beat",         "featured_role": "humanist",    "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 12, "title": "Humanist Closes Their Take",      "featured_role": "humanist",    "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 13, "title": "Provocateur Round 2",             "featured_role": "provocateur", "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 14, "title": "Analyst Rebuts",                  "featured_role": "analyst",     "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 15, "title": "Humanist Reframes Again",         "featured_role": "humanist",    "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 16, "title": "Anchor Pivots to Clash",          "featured_role": "anchor",      "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 17, "title": "Cross Clash A",                   "featured_role": "cross",       "duration": 5, "camera_motion": "static",    "shot": "WIDE_GROUP"},
    {"scene_number": 18, "title": "Cross Clash B",                   "featured_role": "cross",       "duration": 5, "camera_motion": "static",    "shot": "WIDE_GROUP"},
    {"scene_number": 19, "title": "Anchor Calls Spin",               "featured_role": "anchor",      "duration": 5, "camera_motion": "dolly_in",  "shot": "MEDIUM_SOLO"},
    {"scene_number": 20, "title": "Provocateur Final Position",      "featured_role": "provocateur", "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 21, "title": "Analyst Final Position",          "featured_role": "analyst",     "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 22, "title": "Humanist Final Position",         "featured_role": "humanist",    "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 23, "title": "Anchor Reflects",                 "featured_role": "anchor",      "duration": 5, "camera_motion": "static",    "shot": "MEDIUM_SOLO"},
    {"scene_number": 24, "title": "Anchor Close — Pull to Wide",     "featured_role": "anchor",      "duration": 5, "camera_motion": "dolly_out", "shot": "WIDE_GROUP"},
]


def _panel_descriptor(personas: list[dict[str, Any]]) -> str:
    """A one-paragraph fingerprint of the panel, re-quoted into every scene's
    prompt so Seedance renders the same four people in every shot."""
    lines = []
    for p in personas:
        voice = p.get("voice") or {}
        lines.append(
            f"  · {p['name']} ({p['role']}, {voice.get('gender','')}): {p.get('culture','')}. "
            f"{p.get('style','')}"
        )
    return "PANEL (same four people every scene, consistent wardrobe + hair):\n" + "\n".join(lines)


async def compile_script(
    channel: str,
    story: dict[str, Any],
    debate: list[dict[str, Any]],
    personas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile the debate into a 7-scene cinematic script."""
    channel_cfg = channel_or_raise(channel)
    channel_style = channel_cfg["visual_style"]
    debate_text = "\n".join(f"{m['persona_name']}: {m['content']}" for m in debate)
    personas = personas or []
    panel_block = _panel_descriptor(personas) if personas else ""

    spine_hint = json.dumps(SCENE_SPINE, indent=2)

    prompt = f"""You are the director of The Tabloid — a high-energy AI news debate show.
Your job: convert the debate transcript below into a 7-scene broadcast script
that feels cinematic, emotional, and fun. This is entertainment as much as news.

{panel_block}

CHANNEL VISUAL STYLE (apply to every seedance_prompt):
{channel_style}

DEBATE TRANSCRIPT:
{debate_text}

STRUCTURE YOU MUST FOLLOW — do not change scene count, ordering, camera
motion, featured_role, or duration. Fill in seedance_prompt, vo_line, and
emotional_beat for each:

{spine_hint}

SEEDANCE PROMPT GUIDELINES (each scene_prompt):
- The set, desk, panel composition, wardrobe, and lighting are LOCKED via a
  reference image — do NOT re-describe the set or restate panelist looks.
  Stage continuity is handled outside your prompt.
- Focus the prompt on (a) which panelist is speaking, (b) their body language
  and micro-expression as the line lands, (c) their seated position relative
  to the desk (e.g. "leans slightly forward, hand resting on the desk").
- Use the camera_motion from the spine verbatim. The motion enum is strictly
  {{dolly_in, dolly_out, static}} — do not invent other motions.
- Tone is composed and broadcast-polished — Daily Show / The Tabloid grammar.
  Subtle beats over kinetic ones: "steady", "measured", "dry", "considered",
  "a flicker of disbelief". Avoid melodrama words ("fire", "trembling",
  "explosive") — the calm is the point.
- 9:16 vertical composition, no on-screen text (we composite lower thirds).

VOICEOVER (HARD LIMITS — each scene is a 5-second clip):
- Every vo_line must be ≤ 11 words. Count the words before you write it.
  A 12-word line WILL get truncated — that is worse than a short punchy line.
- Scene 1 (cold open wide): "Welcome to The Tabloid. I'm {{anchor_name}}." (6 words)
- Scenes 2-3 (anchor): frames story + sets stakes — one short sentence each.
- Scenes 4-6 (provocateur): three lines that build — open, develop, land.
- Scenes 7-9 (analyst): counter, data drop, close — three sharp clauses.
- Scenes 10-12 (humanist): reframe, hold, close — three sharp clauses.
- Scenes 13-15 (round 2): one punch line each from provocateur/analyst/humanist.
- Scene 16 (anchor pivot): one short sentence framing the clash.
- Scenes 17-18 (cross clash): two short clashing exchanges, each from a different
  guest, named in the line (e.g. "Ravi: 'But the data says—'").
- Scene 19 (anchor calls spin): one sharp framing line.
- Scenes 20-22 (final positions): one closing sentence per guest in order.
- Scene 23 (anchor reflects): one short reflective sentence.
- Scene 24 (anchor close pull-out): "That's The Tabloid." (3 words)
- No semicolons, no "and" chains. One clause, spoken naturally.

INFOGRAPHICS:
- Exactly 4: one stat_card on scene 8 (analyst data drop) and another on scene 14
  (analyst rebuts), each using data from the debate. Two quote_pulls, one on
  scene 6 (provocateur lands the punch) and one on scene 12 (humanist closes).

Return JSON only, matching this shape exactly. The "scenes" array MUST contain
exactly 24 entries (one per spine entry above), in the same order:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "title": "...",
      "featured_role": "anchor",
      "featured_persona_id": "kavitha_rajan",
      "duration": 5,
      "camera_motion": "dolly_in",
      "shot": "WIDE_GROUP",
      "emotional_beat": "anticipation",
      "seedance_prompt": "...",
      "vo_line": "Welcome to The Tabloid..."
    }}
  ],
  "infographics": [
    {{"type": "stat_card",  "scene_index": 7,  "timestamp_in_scene": 1.5, "duration": 3.0, "data": {{"number":"…", "label":"…", "stat_source":"…"}}}},
    {{"type": "stat_card",  "scene_index": 13, "timestamp_in_scene": 1.5, "duration": 3.0, "data": {{"number":"…", "label":"…", "stat_source":"…"}}}},
    {{"type": "quote_pull", "scene_index": 5,  "timestamp_in_scene": 1.0, "duration": 3.5, "data": {{"quote":"…", "speaker":"…"}}}},
    {{"type": "quote_pull", "scene_index": 11, "timestamp_in_scene": 1.0, "duration": 3.5, "data": {{"quote":"…", "speaker":"…"}}}}
  ],
  "vo_script": [
    {{"persona_name": "…", "agent": "anchor", "line": "…", "scene_index": 0}}
  ]
}}

scene_index is 0-based (scene 1 → scene_index 0). Put one VO entry per scene
(24 total). Keep every seedance_prompt under 80 words so the full 24-scene
JSON fits the response budget."""

    # Script output is dense JSON (24 scenes + infographics + VO). Gemini likes
    # 12k-16k tokens here; Seed is more compact. Allow 16k to avoid truncation
    # mid-JSON, which shows up as "Expecting ',' delimiter" at parse time.
    raw = await call_seed2(prompt, temperature=0.65, max_tokens=16000)
    try:
        script = parse_json(raw)
    except Exception as first_err:
        # One retry with a tighter prompt if the model blew the JSON.
        retry_prompt = (
            prompt
            + "\n\nREMINDER: your previous attempt produced invalid JSON. "
            "Return ONLY a single valid JSON object. Keep seedance_prompt and "
            "vo_line entries short enough that the full object fits in the "
            "response budget. No prose, no code fences, no trailing commas."
        )
        raw = await call_seed2(retry_prompt, temperature=0.5, max_tokens=16000)
        try:
            script = parse_json(raw)
        except Exception:
            raise first_err
    _validate(script, personas)
    return script


_ALLOWED_MOTIONS = {"dolly_in", "dolly_out", "static"}


def _validate(script: dict[str, Any], personas: list[dict[str, Any]]) -> None:
    scenes = script.get("scenes") or []
    if len(scenes) != 24:
        raise ValueError(f"Expected 24 scenes, got {len(scenes)}")

    persona_by_role = {p["role"]: p for p in personas}
    persona_ids = {p["id"] for p in personas}
    spine_motions = {s["scene_number"]: s["camera_motion"] for s in SCENE_SPINE}

    for i, s in enumerate(scenes):
        for k in ("seedance_prompt", "camera_motion", "duration", "featured_role"):
            if k not in s:
                raise ValueError(f"Scene {i} missing field {k}")

        # Coerce camera_motion back to the spine value if the model invented
        # one — the rest of the pipeline depends on the 3-motion enum.
        if s["camera_motion"] not in _ALLOWED_MOTIONS:
            scene_num = s.get("scene_number", i + 1)
            s["camera_motion"] = spine_motions.get(scene_num, "static")

        # Backfill featured_persona_id from featured_role if the model forgot.
        role = s.get("featured_role")
        pid = s.get("featured_persona_id")
        if not pid or pid not in persona_ids:
            if role == "cross":
                s["featured_persona_id"] = None  # signals "all four"
            else:
                fallback = persona_by_role.get(role)
                s["featured_persona_id"] = fallback["id"] if fallback else None

    script.setdefault("infographics", [])
    script.setdefault("vo_script", [])
