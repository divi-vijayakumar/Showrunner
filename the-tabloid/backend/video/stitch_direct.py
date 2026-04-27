"""Stitch a direct-mode segment's per-scene mp4s into a single episode.

Two stages:
  1. Build intro/outro "title cards" — still image + audio track (5-7s each).
     Image: the same avatar PNG used as the show's locked panel reference,
     so the cold-open and sign-off feel produced and on-brand.
  2. Concat intro + every scene clip in manifest order + outro.

Re-encodes to a uniform codec/resolution since the intro/outro come from
different sources than the Seedance clips. Output is 720x1280 (9:16),
H.264 + AAC, +faststart for browser playback.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

log = logging.getLogger(__name__)

# Output dimensions match Seedance fast i2v output (720p 9:16).
_OUT_WIDTH = 720
_OUT_HEIGHT = 1280
_OUT_FPS = 30


def _scene_slug(scene_number: Any) -> str:
    """Mirror the slug used by the pipeline to find the right mp4 file.
    Pads ints to 2 digits ("09"), keeps strings as-is ("9b")."""
    try:
        return f"{int(scene_number):02d}"
    except (ValueError, TypeError):
        return str(scene_number)


def _build_titlecard(
    image_path: str,
    audio_path: str,
    out_path: str,
    duration_s: float,
    fade_in: float = 0.3,
    fade_out: float = 0.5,
) -> None:
    """Render a still-image clip with a soundtrack — used for intro + outro.
    Image is scaled/cropped to 720x1280 (9:16) and the audio is faded in
    and out softly so it doesn't pop into/out of the episode."""
    duration_s = max(0.5, float(duration_s))
    fade_out_start = max(0.0, duration_s - fade_out)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", f"{duration_s:.3f}", "-i", image_path,
        "-i", audio_path,
        "-filter_complex",
        (
            # Scale + crop image to exact output 9:16, set frame rate, and
            # ensure SAR/PAR is normalized so concat doesn't choke later.
            f"[0:v]scale={_OUT_WIDTH}:{_OUT_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={_OUT_WIDTH}:{_OUT_HEIGHT},"
            f"fps={_OUT_FPS},setsar=1[v];"
            # Audio: clip to duration, fade in at start, fade out at end.
            f"[1:a]atrim=0:{duration_s:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest", "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _audio_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0


def stitch_direct_segment(
    segment_id: str,
    *,
    segments_dir: str,
    output_dir: str,
    intro_image_path: str | None = None,
    outro_image_path: str | None = None,
    intro_audio_path: str | None = None,
    outro_audio_path: str | None = None,
) -> dict[str, Any]:
    """Concat all scene clips of `segment_id` into one mp4. Optional
    intro/outro title cards (image + audio). Returns metadata about the
    output: path, duration, size, mode, intro/outro presence.

    Args:
      segment_id: e.g. "seg_03a618cd9f"
      segments_dir: absolute path to data/segments/
      output_dir: absolute path to data/videos/
      intro_image_path: PNG/JPG to display during intro audio (the panel
        avatar is a sensible default). If not given AND intro_audio_path
        is set, falls back to a black background.
      outro_image_path: same idea for outro
      intro_audio_path: .mp3/.wav for the intro sting
      outro_audio_path: .mp3/.wav for the outro sting

    Re-encodes when intro/outro are present (we need a uniform codec).
    Falls back to fast stream-copy concat when neither is set.
    """
    seg_dir = os.path.join(segments_dir, segment_id)
    manifest_path = os.path.join(seg_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = json.load(open(manifest_path))
    clips = manifest.get("clips") or []
    if not clips:
        raise RuntimeError(f"manifest has no clips: {segment_id}")

    # Order scene mp4 paths from manifest. Skip clips that aren't ready.
    scene_paths: list[str] = []
    skipped: list[str] = []
    for c in clips:
        sn = c.get("scene_number")
        slug = _scene_slug(sn)
        path = os.path.join(seg_dir, f"scene_{slug}.mp4")
        if c.get("status") != "ready" or not os.path.exists(path):
            skipped.append(str(sn))
            continue
        scene_paths.append(path)

    if not scene_paths:
        raise RuntimeError(f"no ready clips to stitch: {segment_id}")

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{segment_id}_full.mp4")

    intro_path: str | None = None
    outro_path: str | None = None
    has_music = False

    # Build optional intro title card.
    if intro_audio_path and os.path.exists(intro_audio_path):
        intro_path = os.path.join(seg_dir, "_intro.mp4")
        dur = _audio_duration(intro_audio_path)
        # Use the avatar image as the intro visual when one is provided;
        # otherwise the helper falls back to a black field via a colour
        # source (see fallback below).
        img = (
            intro_image_path
            if intro_image_path and os.path.exists(intro_image_path)
            else None
        )
        if img:
            _build_titlecard(img, intro_audio_path, intro_path, dur)
            has_music = True
        else:
            log.warning("intro audio set but no image provided — skipping intro")
            intro_path = None

    # Same for outro.
    if outro_audio_path and os.path.exists(outro_audio_path):
        outro_path = os.path.join(seg_dir, "_outro.mp4")
        dur = _audio_duration(outro_audio_path)
        img = (
            outro_image_path
            if outro_image_path and os.path.exists(outro_image_path)
            else (
                intro_image_path
                if intro_image_path and os.path.exists(intro_image_path)
                else None
            )
        )
        if img:
            _build_titlecard(img, outro_audio_path, outro_path, dur)
            has_music = True
        else:
            log.warning("outro audio set but no image provided — skipping outro")
            outro_path = None

    # Build the concat list in order.
    concat_files: list[str] = []
    if intro_path:
        concat_files.append(intro_path)
    concat_files.extend(scene_paths)
    if outro_path:
        concat_files.append(outro_path)

    list_path = os.path.join(seg_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in concat_files:
            esc = p.replace("'", "'\\''")
            f.write(f"file '{esc}'\n")

    # When we have music (intro/outro), the concat sources have different
    # codec params than the Seedance clips. Stream-copy will fail or
    # produce a broken file — re-encode to a uniform 720x1280 H.264/AAC
    # output. When there's NO music, fast stream-copy works (all sources
    # are direct Seedance i2v output, identical codec).
    mode: str
    if has_music:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", f"scale={_OUT_WIDTH}:{_OUT_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={_OUT_WIDTH}:{_OUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_OUT_FPS}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        mode = "re-encoded with intro/outro music"
    else:
        # Fast path: all clips share codec, stream-copy.
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", "-movflags", "+faststart",
            out_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            mode = "stream-copy (no music)"
        except subprocess.CalledProcessError:
            # Fall back to re-encode if -c copy fails (mismatched codecs).
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                out_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            mode = "re-encoded (codec mismatch fallback)"

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("ffmpeg produced empty output")

    final_dur = _audio_duration(out_path)
    final_size = os.path.getsize(out_path)
    return {
        "segment_id": segment_id,
        "output_path": out_path,
        "duration_seconds": final_dur,
        "size_bytes": final_size,
        "size_mb": round(final_size / (1024 * 1024), 2),
        "mode": mode,
        "clip_count": len(scene_paths),
        "intro": bool(intro_path),
        "outro": bool(outro_path),
        "skipped_scenes": skipped,
    }
