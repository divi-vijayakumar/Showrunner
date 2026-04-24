"""Compile the finished debate into a cinematic broadcast script.

Produces a 7-scene arc that actually looks like a TV panel show:

  1. COLD OPEN — wide podium establishing shot: 4 panelists behind the desk,
     crane-up + slow pan, title plate anchored in. Anchor VO: "Welcome to
     The Tabloid. I'm {anchor}. Tonight —"
  2. ANCHOR INTRO — push-in on the anchor; states the story and the stakes.
  3. PROVOCATEUR TAKE — whip-pan to the provocateur, rim-lit, their sharpest line.
  4. ANALYST COUNTER — dolly to analyst; data vibe, cooler palette.
  5. HUMANIST MOMENT — intimate close on humanist; slow push, warm light.
  6. CLASH — rapid cross-cuts, 4-way energy, the show's heart.
  7. ANCHOR CLOSE — pull back to wide, hold logo. "That's The Tabloid."

Each scene names the persona it features via `persona_id` so the video layer
can pass the right reference portrait into Seedance img2video.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import channel_or_raise
from .llm import call_seed2, parse_json


# Deterministic spine for the script. The LLM fills in the dialogue and visual
# beats, but the camera grammar, featured role, and duration stay fixed so the
# demo always lands.
SCENE_SPINE: list[dict[str, Any]] = [
    {
        "scene_number": 1,
        "title": "Cold Open — Podium Sweep",
        "featured_role": "anchor",
        "duration": 5,
        "camera_motion": "crane_up",
        "shot": "WIDE_GROUP",
    },
    {
        "scene_number": 2,
        "title": "Anchor Intro",
        "featured_role": "anchor",
        "duration": 5,
        "camera_motion": "dolly_in",
        "shot": "MEDIUM_SOLO",
    },
    {
        "scene_number": 3,
        "title": "Provocateur Take",
        "featured_role": "provocateur",
        "duration": 5,
        "camera_motion": "pan_right",
        "shot": "CLOSE_SOLO",
    },
    {
        "scene_number": 4,
        "title": "Analyst Counter",
        "featured_role": "analyst",
        "duration": 5,
        "camera_motion": "orbit_right",
        "shot": "MEDIUM_SOLO",
    },
    {
        "scene_number": 5,
        "title": "Humanist Moment",
        "featured_role": "humanist",
        "duration": 5,
        "camera_motion": "dolly_in",
        "shot": "CLOSE_SOLO",
    },
    {
        "scene_number": 6,
        "title": "Clash",
        "featured_role": "cross",
        "duration": 5,
        "camera_motion": "whip_pan",
        "shot": "FAST_CROSS_CUT",
    },
    {
        "scene_number": 7,
        "title": "Anchor Close",
        "featured_role": "anchor",
        "duration": 5,
        "camera_motion": "dolly_out",
        "shot": "WIDE_GROUP",
    },
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
- Always reopen with: "Broadcast talk-show set: modern curved anchor desk,
  four panelists seated behind it, branded 'THE TABLOID' backlight."
- Restate the four panelists' names and looks so characters stay consistent.
- Name the featured persona explicitly in solo scenes; describe their body
  language and micro-expression as they deliver the line.
- Use the camera_motion from the spine verbatim.
- Lean into emotion: "fire", "steady", "trembling", "laughter", "disarmed".
- 9:16 vertical composition, no on-screen text (we composite lower thirds).

VOICEOVER:
- Scene 1 vo_line must open with "Welcome to The Tabloid. I'm {{anchor_name}}..."
  (substitute the real anchor name from the panel).
- Scene 2+ vo_line should use real lines from the debate transcript, tightened
  for broadcast (2 sentences max).
- Scene 7 vo_line closes with "That's The Tabloid."

INFOGRAPHICS:
- Exactly 2: one stat_card on scene 4 (analyst) using data from the debate,
  one quote_pull on scene 3 or 5 using the most punchy debate line.

Return JSON only, matching this shape exactly:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "title": "...",
      "featured_role": "anchor",
      "featured_persona_id": "kavitha_rajan",
      "duration": 5,
      "camera_motion": "crane_up",
      "shot": "WIDE_GROUP",
      "emotional_beat": "anticipation",
      "seedance_prompt": "...",
      "vo_line": "Welcome to The Tabloid..."
    }}
  ],
  "infographics": [
    {{"type": "stat_card", "scene_index": 3, "timestamp_in_scene": 1.5, "duration": 3.0, "data": {{"number":"…", "label":"…", "stat_source":"…"}}}},
    {{"type": "quote_pull", "scene_index": 2, "timestamp_in_scene": 1.0, "duration": 3.5, "data": {{"quote":"…", "speaker":"…"}}}}
  ],
  "vo_script": [
    {{"persona_name": "…", "agent": "anchor", "line": "…", "scene_index": 0}}
  ]
}}

scene_index is 0-based (scene 1 → scene_index 0). Put one VO entry per scene."""

    raw = await call_seed2(prompt, temperature=0.65, max_tokens=2200)
    script = parse_json(raw)
    _validate(script, personas)
    return script


def _validate(script: dict[str, Any], personas: list[dict[str, Any]]) -> None:
    scenes = script.get("scenes") or []
    if len(scenes) != 7:
        raise ValueError(f"Expected 7 scenes, got {len(scenes)}")

    persona_by_role = {p["role"]: p for p in personas}
    persona_ids = {p["id"] for p in personas}

    for i, s in enumerate(scenes):
        for k in ("seedance_prompt", "camera_motion", "duration", "featured_role"):
            if k not in s:
                raise ValueError(f"Scene {i} missing field {k}")

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
