"""Stitch a direct-mode segment's per-scene mp4s into a single episode.

Thin adapter: reads the segment's manifest, collects the local scene
mp4 paths in manifest order (skipping clips that aren't ready), then
delegates to the panel-debate editor agent's `assemble_episode` for the
actual ffmpeg work.

The editor runs two passes:
  1. xfade + acrossfade across all scene clips (smooth cuts, no audio
     clicks). Re-encodes video.
  2. Intro/outro music overlay with `-c:v copy` (lip sync preserved).

Output: 720x1280 (9:16), H.264 + AAC, +faststart for browser playback.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ...templates.panel_debate.agents.editor import assemble_episode

log = logging.getLogger(__name__)


def _scene_slug(scene_number: Any) -> str:
    """Mirror the slug used by the pipeline to find the right mp4 file.
    Pads ints to 2 digits ("09"), keeps strings as-is ("9b")."""
    try:
        return f"{int(scene_number):02d}"
    except (ValueError, TypeError):
        return str(scene_number)


def stitch_direct_segment(
    segment_id: str,
    *,
    segments_dir: str,
    output_dir: str,
    outro_audio_path: str | None = None,
    outro_overlap_seconds: float = 2.0,
    video_blend_duration: float = 0.1,
    music_volume: float = 0.55,
) -> dict[str, Any]:
    """Two-pass episode assembly for a direct-mode segment.

    Args:
      segment_id: e.g. "seg_03a618cd9f"
      segments_dir: absolute path to data/segments/
      output_dir:   absolute path to data/videos/
      outro_audio_path: optional .mp3/.wav for the closing sting. None
        skips the outro pass entirely.
      outro_overlap_seconds:  outro plays over the last N seconds of the
        episode (also bounds the outro length — only the LAST N seconds
        of the source mp3 are used). Default 2s.
      video_blend_duration:   xfade duration per cut between scene clips
        (default 0.1s ≈ 3 frames at 30fps).
      music_volume:           how much to duck the outro under dialogue
        during the overlap (default 0.55 ≈ -5 dB).

    No intro music — see editor.py docstring for the lip-sync reason.

    Returns metadata about the output: path, duration, size, modes,
    outro presence, skipped scenes."""
    seg_dir = os.path.join(segments_dir, segment_id)
    manifest_path = os.path.join(seg_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = json.load(open(manifest_path))
    clip_entries = manifest.get("clips") or []
    if not clip_entries:
        raise RuntimeError(f"manifest has no clips: {segment_id}")

    # Collect ready scene mp4 paths in manifest order. Skip clips that
    # aren't ready or whose file is missing on disk.
    scene_paths: list[str] = []
    skipped: list[str] = []
    for c in clip_entries:
        sn = c.get("scene_number")
        slug = _scene_slug(sn)
        path = os.path.join(seg_dir, f"scene_{slug}.mp4")
        if c.get("status") != "ready" or not os.path.exists(path):
            skipped.append(str(sn))
            continue
        scene_paths.append(path)

    if not scene_paths:
        raise RuntimeError(f"no ready clips to stitch: {segment_id}")
    if len(scene_paths) < 2:
        raise RuntimeError(
            f"need ≥2 ready clips for xfade blend, got {len(scene_paths)}"
        )

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{segment_id}_full.mp4")
    work_dir = os.path.join(seg_dir, ".editor")

    # Outro music is optional — pass None if missing on disk.
    outro = outro_audio_path if (
        outro_audio_path and os.path.exists(outro_audio_path)
    ) else None
    if outro_audio_path and not outro:
        log.warning("outro audio path does not exist: %s", outro_audio_path)

    result = assemble_episode(
        clips=scene_paths,
        output_path=out_path,
        work_dir=work_dir,
        outro_music=outro,
        outro_overlap_seconds=outro_overlap_seconds,
        video_blend_duration=video_blend_duration,
        music_volume=music_volume,
    )

    return {
        "segment_id": segment_id,
        **result,
        "skipped_scenes": skipped,
    }
