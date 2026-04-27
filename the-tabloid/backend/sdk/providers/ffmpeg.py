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


def _has_audio_stream(path: str) -> bool:
    """True if the file has at least one audio stream. We use this to
    detect Seedance's native-audio output and avoid clobbering it."""
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return "audio" in r.stdout


async def stitch_podcast(
    audio_urls: list[str],
    *,
    headline: str,
    channel_label: str,
) -> str:
    """Concat a list of TTS audio clips into one mp3 with small beat gaps
    between speakers. Returns local path. No video work."""
    work_dir = f"/tmp/tabloid_podcast_{uuid.uuid4().hex[:8]}"
    os.makedirs(work_dir, exist_ok=True)

    # 1) Download all TTS clips; normalise to mp3 at a common sample rate.
    normalized: list[str] = []
    for i, url in enumerate(audio_urls):
        raw = os.path.join(work_dir, f"raw_{i:02d}.mp3")
        await download_file(url, raw)
        out = os.path.join(work_dir, f"norm_{i:02d}.mp3")
        _run([
            "ffmpeg", "-y",
            "-i", raw,
            "-ar", "44100", "-ac", "2",
            "-codec:a", "libmp3lame", "-b:a", "160k",
            out,
        ])
        normalized.append(out)

    # 2) 300ms silent beat between speakers — feels natural, stops the
    # episode sounding like a rapid-fire monologue.
    beat_path = os.path.join(work_dir, "beat.mp3")
    _run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "0.3",
        "-codec:a", "libmp3lame", "-b:a", "160k",
        beat_path,
    ])

    # 3) Build concat list: clip / beat / clip / beat / ... / clip
    concat_list = os.path.join(work_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for i, p in enumerate(normalized):
            f.write(f"file '{p}'\n")
            if i != len(normalized) - 1:
                f.write(f"file '{beat_path}'\n")

    final_out = os.path.join(work_dir, "episode.mp3")
    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-codec:a", "libmp3lame", "-b:a", "192k",
        "-metadata", f"title={headline[:160]}",
        "-metadata", f"album=The Tabloid · {channel_label}",
        "-metadata", "artist=The Tabloid",
        final_out,
    ])

    return final_out


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

    # 3) Audio per scene. Three cases in priority order:
    #    a) External VO URL for this scene → mix over the visual.
    #    b) The source clip already has an audio track (Seedance native audio
    #       — generate_audio=True) → re-encode video + keep its audio.
    #    c) No audio anywhere → synthesize silence so concat stays aligned.
    SCENE_SECS = 5
    with_audio: list[str] = []
    for i, src in enumerate(clip_paths):
        vsrc = normalized[i]  # normalized has no audio (we stripped it)
        has_native = _has_audio_stream(src)
        out = os.path.join(work_dir, f"scene_{i:02d}.mp4")
        if i in vo_by_scene:
            _run(
                [
                    "ffmpeg", "-y",
                    "-i", vsrc,
                    "-i", vo_by_scene[i],
                    "-filter_complex", "[1:a]apad[a]",
                    "-map", "0:v",
                    "-map", "[a]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-t", str(SCENE_SECS),
                    out,
                ]
            )
        elif has_native:
            # Bring in video from the normalized (visually-correct) stream
            # and the native audio from the original downloaded clip.
            _run(
                [
                    "ffmpeg", "-y",
                    "-i", vsrc,
                    "-i", src,
                    "-map", "0:v",
                    "-map", "1:a:0",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "160k",
                    "-t", str(SCENE_SECS),
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
                    "-t", str(SCENE_SECS),
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
