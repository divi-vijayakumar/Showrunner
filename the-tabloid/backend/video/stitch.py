"""ffmpeg pipeline: download clips + VO + overlays, stitch into a 30s 9:16 segment.

Shapes the output as a broadcast-feel vertical video:
- 6 × 5s Seedance clips, cut together
- per-scene VO laid over the clip audio (silent from Seedance)
- persistent news ticker + channel bug
- infographic overlays with in/out timing
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import uuid
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _has_drawtext() -> bool:
    """Some ffmpeg builds (notably homebrew's default on macOS) ship without
    libfreetype, so the drawtext filter is missing. When that's the case we
    silently skip ticker + channel bug overlays instead of failing the job."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    if " drawtext " in r.stdout:
        return True
    log.warning(
        "ffmpeg is missing the 'drawtext' filter — ticker + channel bug will be skipped. "
        "Install an ffmpeg built with libfreetype to enable them."
    )
    return False


async def download_file(url: str, dest: str) -> None:
    """Download any URL to a local path. Supports file:// for mock mode."""
    if url.startswith("file://"):
        src = url[len("file://"):]
        if os.path.abspath(src) != os.path.abspath(dest):
            with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
                fdst.write(fsrc.read())
        return

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)


def _run(cmd: list[str]) -> None:
    """Run an ffmpeg command; raise with useful output on failure."""
    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n{result.stderr[-800:]}"
        )


async def stitch_segment(
    clips: list[str],
    vo_clips: list[dict],
    overlays: list[dict],
    ticker_text: str,
    channel_label: str,
) -> str:
    """Assemble the final segment and return its local path."""
    work_dir = f"/tmp/tabloid_segment_{uuid.uuid4().hex[:8]}"
    os.makedirs(work_dir, exist_ok=True)

    # 1) Download clips + VO audio
    clip_paths: list[str] = []
    for i, url in enumerate(clips):
        path = os.path.join(work_dir, f"clip_{i:02d}.mp4")
        await download_file(url, path)
        clip_paths.append(path)

    vo_by_scene: dict[int, str] = {}
    for vo in vo_clips:
        idx = int(vo["scene_index"])
        path = os.path.join(work_dir, f"vo_{idx:02d}.mp3")
        await download_file(vo["audio_url"], path)
        vo_by_scene[idx] = path

    # 2) Normalise every clip to 1080x1920 @ 30fps, 5s, no audio
    normalized: list[str] = []
    for i, src in enumerate(clip_paths):
        out = os.path.join(work_dir, f"norm_{i:02d}.mp4")
        _run(
            [
                "ffmpeg", "-y",
                "-i", src,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
                "-t", "5",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-an",
                out,
            ]
        )
        normalized.append(out)

    # 3) Mix VO onto each scene (silent track for scenes with no VO so concat stays aligned)
    with_audio: list[str] = []
    for i, vsrc in enumerate(normalized):
        out = os.path.join(work_dir, f"scene_{i:02d}.mp4")
        if i in vo_by_scene:
            _run(
                [
                    "ffmpeg", "-y",
                    "-i", vsrc,
                    "-i", vo_by_scene[i],
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    out,
                ]
            )
        else:
            _run(
                [
                    "ffmpeg", "-y",
                    "-i", vsrc,
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=stereo",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    out,
                ]
            )
        with_audio.append(out)

    # 4) Concat scenes
    concat_list = os.path.join(work_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in with_audio:
            # ffmpeg concat demuxer wants escaped single quotes in paths — none of ours have them.
            f.write(f"file '{p}'\n")

    concat_out = os.path.join(work_dir, "concat.mp4")
    _run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            concat_out,
        ]
    )

    # 5) Apply ticker + channel bug + infographic overlays
    final_out = os.path.join(work_dir, "final.mp4")
    overlay_inputs: list[str] = []
    for ov in overlays:
        overlay_inputs += ["-i", ov["path"]]

    filter_complex = _build_filter_complex(overlays, ticker_text, channel_label)

    cmd = [
        "ffmpeg", "-y",
        "-i", concat_out,
        *overlay_inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        final_out,
    ]
    _run(cmd)

    return final_out


def _escape_drawtext(text: str) -> str:
    """ffmpeg drawtext escapes: \\, :, ', %."""
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def _build_filter_complex(
    overlays: list[dict],
    ticker_text: str,
    channel_label: str,
) -> str:
    """Compose drawtext (ticker + bug) and per-overlay compositing."""
    if _has_drawtext():
        ticker = _escape_drawtext(ticker_text)
        bug = _escape_drawtext(channel_label.upper())
        chain = (
            "[0:v]"
            f"drawtext=text='{ticker}':"
            "fontsize=26:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=10:"
            "x='w-mod(t*140\\,w+tw)':y=h-70"
            ","
            f"drawtext=text='THE TABLOID · {bug}':"
            "fontsize=22:fontcolor=white:"
            "box=1:boxcolor=0x00dbe9@0.9:boxborderw=8:"
            "x=w-tw-24:y=28"
            "[v0]"
        )
    else:
        # Graceful fallback: pass the main video through without ticker/bug
        chain = "[0:v]copy[v0]"

    current = "v0"
    for i, ov in enumerate(overlays):
        t_start = int(ov["scene_index"]) * 5 + float(ov["timestamp_in_scene"])
        t_end = t_start + float(ov["duration"])
        nxt = f"v{i+1}"
        # bottom-left, above the ticker (which sits around h-70)
        chain += (
            f";[{current}][{i+1}:v]overlay=24:H-h-100:"
            f"enable='between(t,{t_start:.2f},{t_end:.2f})'[{nxt}]"
        )
        current = nxt

    chain += f";[{current}]copy[vout]"
    return chain
