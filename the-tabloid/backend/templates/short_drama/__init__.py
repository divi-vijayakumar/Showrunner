"""Short Drama template — chat-style brief + uploaded character/set photos →
Pixar-stylized 2-min film with native Tamil/English audio.

Pipeline:
  1. stylize.pixarify_characters → Pixar-styled character anchors (Fal Seedream edit i2i)
  2. set_designer.generate_set_masters → Pixar-styled set stills (ARK Seedream t2i)
  3. writer.scene_plan_from_brief → typed scene plan (Seed LLM)
  4. director.render_clip per scene → Seedance 2.0 with native audio (ARK direct, 4-key pool)
  5. editor.stitch_film → xfade blend + outro music + final mp4

Sibling to `panel_debate` — does NOT touch panel_debate code paths."""
from __future__ import annotations

from .pipeline import generate_drama  # noqa: F401
