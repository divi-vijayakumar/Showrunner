"""Compile the completed debate into a 6-scene broadcast script.

Produces:
- `scenes`: 6 Seedance prompts, one per clip
- `infographics`: overlay cards (stat/quote/context) with timing
- `vo_script`: per-line voiceover text aligned to scene indexes
"""
from __future__ import annotations

from typing import Any

from ..config import channel_or_raise
from .llm import call_seed2, parse_json


async def compile_script(
    channel: str,
    story: dict[str, Any],
    debate: list[dict[str, Any]],
) -> dict[str, Any]:
    channel_cfg = channel_or_raise(channel)
    channel_style = channel_cfg["visual_style"]
    debate_text = "\n".join(f"{m['persona_name']}: {m['content']}" for m in debate)

    prompt = f"""You are a broadcast director. Convert this news debate into a 6-scene video script.

DEBATE TRANSCRIPT:
{debate_text}

CHANNEL VISUAL STYLE: {channel_style}

Generate a 6-scene script. Each scene = one Seedance video clip (5 seconds each).

Return JSON only:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "title": "Cold Open",
      "duration": 5,
      "description": "What happens visually",
      "seedance_prompt": "Detailed cinematic prompt. Must include: subject + action + environment + camera motion + mood. Style: {channel_style}",
      "camera_motion": "dolly_in",
      "audio_note": "ambient or silent",
      "vo_line": "voiceover line for this scene if any"
    }}
  ],
  "infographics": [
    {{
      "type": "stat_card | quote_pull | context_card | quick_poll",
      "scene_index": 1,
      "timestamp_in_scene": 2.0,
      "duration": 4.0,
      "data": {{}}
    }}
  ],
  "vo_script": [
    {{
      "persona_name": "name",
      "agent": "anchor",
      "line": "spoken line",
      "scene_index": 0
    }}
  ]
}}

Scene types:
1. Cold Open — establishing b-roll of story topic
2. Provocateur take — visual matching their argument
3. Analyst data — clean visual, data-driven b-roll
4. Humanist moment — intimate human scene
5. Clash — fast-cut energy
6. Anchor close — resolution, logo hold"""

    raw = await call_seed2(prompt, temperature=0.7, max_tokens=1600)
    script = parse_json(raw)
    _validate(script)
    return script


def _validate(script: dict[str, Any]) -> None:
    scenes = script.get("scenes") or []
    if len(scenes) != 6:
        raise ValueError(f"Expected 6 scenes, got {len(scenes)}")
    for i, s in enumerate(scenes):
        for k in ("seedance_prompt", "camera_motion", "duration"):
            if k not in s:
                raise ValueError(f"Scene {i} missing field {k}")
    script.setdefault("infographics", [])
    script.setdefault("vo_script", [])
