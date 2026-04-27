"""Editor agent — episode assembly for the panel-debate template.

Three-pass design (order matters):

  Pass 1 — `blend_clips_with_xfade`
    Concat scene clips with 0.1s xfade + acrossfade at every cut.
    Eliminates codec-boundary flicker (each Seedance clip has its own
    GOP) and audio clicks at boundaries. Re-encodes video to libx264.
    Cost is paid once.

  Pass 2 — `overlay_music`
    Layer outro music over the LAST N seconds of the dialogue track.
    Stream-copies the video (`-c:v copy`), so lip sync is preserved
    because dialogue audio is NOT delayed — video and audio both start
    at t=0.

  Pass 3 — `apply_graphics_pass`  (optional, requires manifest)
    Composite broadcast graphics in one filter graph: brand bug
    (always-on top-right), segment title (one-shot at start with
    fade), per-scene lower thirds (panelist name+role tag, 0.5s
    after scene start, 3s hold), and burned-in captions (SRT
    generated from manifest VO lines). Re-encodes video; audio is
    `-c:a copy` so the outro mix from pass 2 is preserved unchanged.

Why no intro music: layering an intro requires `adelay`-ing the
dialogue audio so it starts when the intro fades out. But `-c:v copy`
does NOT shift the video stream's timestamps to match — so video plays
during the intro window and the mouth runs ahead of the words by the
delay amount. Adding intro music safely requires re-encoding the video
in pass 2 with `tpad` (defeats the lip-sync goal) or pre-pending a
black-with-music intro clip in pass 1. For now, the cold open is just
clip 1 itself.

The agent's surface is `assemble_episode(clips, outro_music, manifest)`.
ffmpeg is the current backend; the interface is a pure timeline
operation and could swap to OTIO / FCPXML / an actual NLE later
without changing call sites.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ....sdk.providers.ffmpeg import blend_clips_with_xfade, overlay_music
from .graphics import apply_graphics_pass, build_graphics_package

log = logging.getLogger(__name__)


def assemble_episode(
    clips: list[str],
    *,
    output_path: str,
    work_dir: str,
    outro_music: str | None = None,
    outro_overlap_seconds: float = 2.0,
    video_blend_duration: float = 0.1,
    music_volume: float = 0.55,
    manifest: dict[str, Any] | None = None,
    repo_root: str | None = None,
    enable_graphics: bool = False,
) -> dict[str, Any]:
    """Build the final episode mp4 from per-scene clips + optional outro
    + optional broadcast graphics package.

    Args:
      clips: ordered list of local mp4 paths (one per scene).
      output_path: where to write the final mp4.
      work_dir: scratch dir for the intermediate mp4s (Pass 1, Pass 2).
      outro_music: optional mp3/wav. Trimmed to its last
        `outro_overlap_seconds` and laid over the last seconds of the
        dialogue. None skips Pass 2.
      outro_overlap_seconds: how many seconds of dialogue the outro
        overlaps with at the end (also bounds the outro length).
      video_blend_duration: xfade dissolve length per cut. 0.1s ≈ 3
        frames at 30fps — subliminal, reads as a hard cut.
      music_volume: volume multiplier on the outro track (0.55 ≈ -5 dB)
        so it sits under the dialogue, not over it.
      manifest: segment manifest dict. Required for Pass 3 (the
        graphics package reads `clips`, `panel`, `story` from it for
        per-scene lower-third + caption timing). Pass None to skip
        graphics.
      repo_root: absolute path to repo root, so the graphics module
        can look up designer-supplied assets under
        `<repo_root>/assets/graphics/`. Required when enable_graphics.
      enable_graphics: when True (and manifest + repo_root are given),
        compose brand bug + segment title + per-scene lower thirds +
        burned-in captions onto the episode in Pass 3.

    Returns:
      Combined dict from all passes: output_path, duration_seconds,
      size_bytes, size_mb, clip_count, blend_mode, music_mode, outro,
      and graphics layers applied.
    """
    if len(clips) < 2:
        raise ValueError(f"assemble_episode needs ≥2 clips, got {len(clips)}")

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    blended_path = os.path.join(work_dir, "clips_blended.mp4")

    # Pass 1: video xfade + audio acrossfade across all clips.
    log.info(
        "editor: blending %d clips with %.3fs xfade → %s",
        len(clips), video_blend_duration, blended_path,
    )
    blend_result = blend_clips_with_xfade(
        clips=clips,
        output_path=blended_path,
        xfade_duration=video_blend_duration,
    )

    # Pass 2: outro music over the last N seconds of the blended dialogue.
    # If no outro music, copy blended → audio_mixed_path (so Pass 3 has a
    # consistent input regardless of whether the outro pass ran).
    audio_mixed_path = (
        os.path.join(work_dir, "clips_with_outro.mp4")
        if outro_music else blended_path
    )
    music_result: dict[str, Any]
    if outro_music:
        log.info("editor: overlaying outro music → %s", audio_mixed_path)
        music_result = overlay_music(
            video_path=blended_path,
            output_path=audio_mixed_path,
            outro_music=outro_music,
            outro_overlap_seconds=outro_overlap_seconds,
            music_volume=music_volume,
        )
    else:
        music_result = {
            "mode": "none",
            "outro": False,
            "duration_seconds": blend_result.get("duration_seconds", 0),
        }

    # Pass 3: broadcast graphics. Optional; when off (or manifest missing),
    # the audio-mixed mp4 IS the final episode.
    do_graphics = enable_graphics and manifest is not None and repo_root is not None
    if not do_graphics:
        if audio_mixed_path != output_path:
            os.replace(audio_mixed_path, output_path)
        size = os.path.getsize(output_path)
        return {
            "output_path": output_path,
            "duration_seconds": music_result["duration_seconds"],
            "size_bytes": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "clip_count": blend_result["clip_count"],
            "blend_mode": blend_result["mode"],
            "music_mode": music_result["mode"],
            "outro": music_result.get("outro", False),
            "outro_overlap_seconds": music_result.get("outro_overlap_seconds", 0),
            "graphics": "none",
        }

    log.info("editor: building graphics package + applying overlays")
    pkg = build_graphics_package(
        manifest=manifest,
        clip_paths=clips,
        repo_root=repo_root,
        work_dir=work_dir,
        xfade_duration=video_blend_duration,
    )
    graphics_result = apply_graphics_pass(
        video_path=audio_mixed_path,
        output_path=output_path,
        pkg=pkg,
        work_dir=work_dir,
    )

    size = graphics_result["size_bytes"]
    # Re-probe the final container's duration (graphics pass shouldn't
    # change duration but probe to be honest).
    from ....sdk.providers.ffmpeg import probe_duration
    final_dur = probe_duration(output_path)

    return {
        "output_path": output_path,
        "duration_seconds": final_dur,
        "size_bytes": size,
        "size_mb": round(int(size) / (1024 * 1024), 2),
        "clip_count": blend_result["clip_count"],
        "blend_mode": blend_result["mode"],
        "music_mode": music_result["mode"],
        "outro": music_result.get("outro", False),
        "outro_overlap_seconds": music_result.get("outro_overlap_seconds", 0),
        "graphics": graphics_result["graphics"],
    }
