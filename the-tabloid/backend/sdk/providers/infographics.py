"""Render broadcast chrome overlays (stat cards, quote pulls, context cards) as PNGs
that ffmpeg can composite onto the final video.
"""
from __future__ import annotations

import html
import logging
import os
from string import Template
from typing import Any

log = logging.getLogger(__name__)


_STAT_CARD = Template(
    """
<svg width="400" height="120" xmlns="http://www.w3.org/2000/svg">
  <rect width="400" height="120" rx="8" fill="white" fill-opacity="0.92"/>
  <text x="20" y="55" font-family="Arial Black, Helvetica, sans-serif" font-size="42" fill="#111">$number</text>
  <text x="20" y="85" font-family="Arial, Helvetica, sans-serif" font-size="16" fill="#444">$label</text>
  <text x="20" y="108" font-family="Arial, Helvetica, sans-serif" font-size="11" fill="#888">SOURCE: $source</text>
</svg>
"""
)


_QUOTE_CARD = Template(
    """
<svg width="560" height="90" xmlns="http://www.w3.org/2000/svg">
  <rect width="560" height="90" rx="4" fill="#111" fill-opacity="0.85"/>
  <rect width="4" height="90" rx="2" fill="#00dbe9"/>
  <text x="20" y="38" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="white">&#8220;$quote&#8221;</text>
  <text x="20" y="68" font-family="Arial Black, Helvetica, sans-serif" font-size="12" fill="#00dbe9">— $speaker</text>
</svg>
"""
)


_CONTEXT_CARD = Template(
    """
<svg width="560" height="110" xmlns="http://www.w3.org/2000/svg">
  <rect width="560" height="110" rx="4" fill="#1a1a1d" fill-opacity="0.9"/>
  <text x="20" y="32" font-family="Arial Black, Helvetica, sans-serif" font-size="13" fill="#00dbe9">CONTEXT</text>
  <text x="20" y="60" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="white">$line1</text>
  <text x="20" y="85" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="white">$line2</text>
</svg>
"""
)


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def render_infographics(
    infographics: list[dict[str, Any]],
    story: dict[str, Any],
    out_dir: str = "/tmp/tabloid_overlays",
) -> list[dict[str, Any]]:
    """Turn infographic specs into PNG files ready for ffmpeg overlay."""
    try:
        import cairosvg
    except ImportError:
        log.warning("cairosvg not installed — skipping overlay rendering")
        return []

    os.makedirs(out_dir, exist_ok=True)
    rendered: list[dict[str, Any]] = []

    for idx, ig in enumerate(infographics):
        svg = _svg_for(ig, story)
        if not svg:
            continue

        png_path = os.path.join(out_dir, f"overlay_{idx:02d}_{ig.get('type', 'card')}.png")
        try:
            cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=png_path, output_width=400)
        except Exception as exc:
            log.warning("overlay render failed for %s: %s", ig.get("type"), exc)
            continue

        rendered.append(
            {
                "path": png_path,
                "scene_index": int(ig.get("scene_index", 0)),
                "timestamp_in_scene": float(ig.get("timestamp_in_scene", 1.0)),
                "duration": float(ig.get("duration", 3.0)),
            }
        )

    return rendered


def _svg_for(ig: dict[str, Any], story: dict[str, Any]) -> str | None:
    typ = ig.get("type")
    data = ig.get("data") or {}
    ig_story = story.get("infographic_data") or {}

    if typ == "stat_card":
        number = data.get("number") or ig_story.get("key_stat") or ""
        label = _truncate(data.get("label") or ig_story.get("stat_source") or "", 50)
        source = _truncate(data.get("stat_source") or ig_story.get("stat_source") or "", 42)
        if not number:
            return None
        return _STAT_CARD.substitute(
            number=html.escape(str(number)),
            label=html.escape(label),
            source=html.escape(source),
        )

    if typ == "quote_pull":
        quote = _truncate(data.get("quote") or "", 80)
        speaker = _truncate(data.get("speaker") or "", 40)
        if not quote:
            return None
        return _QUOTE_CARD.substitute(quote=html.escape(quote), speaker=html.escape(speaker))

    if typ == "context_card":
        ctx = data.get("text") or ig_story.get("context") or ""
        line1 = _truncate(ctx, 70)
        line2 = _truncate(ctx[70:] if len(ctx) > 70 else "", 70)
        if not line1:
            return None
        return _CONTEXT_CARD.substitute(
            line1=html.escape(line1),
            line2=html.escape(line2),
        )

    return None
