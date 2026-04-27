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


def probe_duration(path: str) -> float:
    """Return container duration via ffprobe. 0.0 on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


# -- Episode assembly: two-pass editor (xfade blend, then music overlay) ----
#
# See sdk/providers/ffmpeg.py docstring at top of file for the design
# rationale. Order matters: blend first (re-encodes video to fix codec
# boundary flicker + audio clicks), then music overlay (uses -c:v copy so
# lip sync is preserved).

def blend_clips_with_xfade(
    clips: list[str],
    *,
    output_path: str,
    xfade_duration: float = 0.1,
    transition: str = "fade",
    crf: int = 18,
    preset: str = "slow",
    target_width: int = 720,
    target_height: int = 1280,
    target_fps: int = 30,
) -> dict[str, object]:
    """Concat scene clips with a short xfade dissolve at every cut.

    Why xfade rather than -c copy concat:
      - Codec boundary flicker. Each Seedance clip has its own GOP
        structure; concat lands clip N+1 on a fresh I-frame with a new
        compression baseline. Even when the boundary frames are pixel-
        identical from the model's POV, the encoder's quantization
        differs, producing a 1-frame brightness/saturation pop.
      - Audio clicks. Seedance occasionally emits sub-millisecond audio
        gaps or non-zero-crossing cuts at clip boundaries. The brain
        fuses audio + visual transients, so audio clicks make a clean
        visual cut feel like flicker.

    A 0.1s (~3 frames at 30fps) cross-dissolve smooths both. Long enough
    to absorb the codec boundary; short enough to read as a hard cut.

    Variable clip durations are handled via ffprobe — offsets are computed
    from actual durations. Skyroot has 11×10s + 2×5s = 13 clips; ints/
    floats are fine.

    Re-encodes the whole stream (libx264 / aac). Stream-copy is
    incompatible with xfade since the filter touches frames in the
    boundary region.

    Returns a dict with output_path, duration_seconds, size_bytes,
    clip_count, and a `mode` describing what ran.
    """
    if len(clips) < 2:
        raise ValueError(f"blend_clips_with_xfade needs ≥2 clips, got {len(clips)}")
    for c in clips:
        if not os.path.exists(c):
            raise FileNotFoundError(f"clip not found: {c}")

    durations = [probe_duration(c) for c in clips]
    if any(d <= 0 for d in durations):
        raise RuntimeError(
            f"could not probe duration for one or more clips: {list(zip(clips, durations))}"
        )

    # offset(n) is the time in the OUTPUT at which the xfade between the
    # nth output (clips 0..n combined via prior xfades) and clip n+1
    # should begin. After n prior xfades, each shaving xfade_duration off
    # the cumulative timeline, the (n+1)th xfade begins at:
    #   cumulative_input_duration[0..n] - (n+1) * xfade_duration
    offsets: list[float] = []
    cum = 0.0
    for i in range(len(clips) - 1):
        cum += durations[i]
        offsets.append(cum - (i + 1) * xfade_duration)

    n = len(clips)

    # Per-input video normalization. Seedance occasionally emits clips with
    # tiny resolution drift or non-1 SAR; xfade requires both inputs to
    # match exactly. Force scale + pad + setsar + fps on every input.
    chains: list[str] = []
    for i in range(n):
        chains.append(
            f"[{i}:v]scale={target_width}:{target_height}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={target_fps},format=yuv420p[v{i}n]"
        )

    # Pairwise video xfade.
    last_v = "[v0n]"
    for i in range(n - 1):
        out_label = "[vout]" if i == n - 2 else f"[v{i:02d}x]"
        chains.append(
            f"{last_v}[v{i+1}n]xfade=transition={transition}:"
            f"duration={xfade_duration:.3f}:offset={offsets[i]:.3f}{out_label}"
        )
        last_v = out_label

    # Audio: normalize sample rate first, then pairwise acrossfade. Forces
    # 48kHz/stereo so amix in the music-overlay pass doesn't have to
    # reconcile rate mismatches.
    for i in range(n):
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}n]")

    last_a = "[a0n]"
    for i in range(n - 1):
        out_label = "[aout]" if i == n - 2 else f"[a{i:02d}x]"
        chains.append(
            f"{last_a}[a{i+1}n]acrossfade=d={xfade_duration:.3f}{out_label}"
        )
        last_a = out_label

    filter_complex = ";".join(chains)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd.extend(["-i", c])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"blend_clips_with_xfade ffmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr[-1200:]}"
        )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("blend_clips_with_xfade produced empty output")

    return {
        "output_path": output_path,
        "duration_seconds": probe_duration(output_path),
        "size_bytes": os.path.getsize(output_path),
        "clip_count": n,
        "mode": f"xfade {xfade_duration}s × {n - 1} cuts (re-encoded)",
    }


def overlay_music(
    *,
    video_path: str,
    output_path: str,
    outro_music: str | None = None,
    outro_overlap_seconds: float = 2.0,
    music_volume: float = 0.55,
    mix_volume: float = 1.5,
) -> dict[str, object]:
    """Layer an outro sting over the LAST `outro_overlap_seconds` of the
    dialogue video. Stream-copies the video (`-c:v copy`) — lip sync is
    mathematically preserved because the dialogue audio is NOT delayed:
    video and audio both start at t=0 of the output container.

    Audio timeline:
        dialogue plays from t=0 to t=clips_dur (untouched)
        outro music: trimmed to its LAST outro_overlap_seconds, fades in
          from 0, starts at t=(clips_dur - outro_overlap_seconds), ends
          at t=clips_dur. Output total length stays = clips_dur.

    NOTE: an earlier version supported intro music with `adelay` on the
    dialogue audio. That broke lip sync because `-c:v copy` does NOT
    shift the video stream's timestamps to match the delayed dialogue
    — so the mouth ran ahead of the words by `intro_dur - intro_overlap`
    seconds. To add intro music safely you'd have to either re-encode
    the video with `tpad` (defeats the lip-sync-preserving goal) or
    concat a black-with-music intro clip in pass 1. Removed for now;
    cold-open intro is just clip 1 itself.

    With `outro_music=None`, the video is stream-copied to output (no
    audio change).

    `music_volume` (default 0.55 ≈ -5 dB) ducks the outro under any
    dialogue still playing during the overlap. `mix_volume` (default
    1.5) compensates for amix's automatic ~1/N reduction.

    NOTE on filter order: afade is applied BEFORE adelay so the fade-in
    operates on the music itself, not on the silent padding adelay
    prepends. Reversing the order silently fades the silence and leaves
    the music starting at full volume.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    clips_dur = probe_duration(video_path)
    if clips_dur <= 0:
        raise RuntimeError(f"could not probe duration of {video_path}")

    have_outro = bool(outro_music) and os.path.exists(outro_music or "")

    if not have_outro:
        # Nothing to mix; just rewrap the container so output format is
        # consistent regardless of input.
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path, "-c", "copy",
            "-movflags", "+faststart", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"copy failed: {result.stderr[-600:]}")
        return {
            "output_path": output_path,
            "duration_seconds": clips_dur,
            "size_bytes": os.path.getsize(output_path),
            "mode": "video copy (no music)",
            "outro": False,
        }

    outro_dur = probe_duration(outro_music)
    # Take the LAST outro_overlap_seconds of the outro track. If the
    # outro file is shorter than the overlap window, just use the whole
    # thing — atrim with start=0 is a no-op.
    outro_trim_start = max(0.0, outro_dur - outro_overlap_seconds)
    # Where the outro music begins on the final timeline.
    outro_start = max(0.0, clips_dur - outro_overlap_seconds)
    outro_start_ms = int(round(outro_start * 1000))

    # Two inputs: video + outro. Dialogue stays at t=0 (no adelay).
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-i", outro_music,
    ]

    # Filter graph:
    #   [0:a] dialogue, untouched (just normalize sample rate)
    #   [1:a] outro: atrim to last N seconds (resets to 0..N), fade in,
    #         delay to outro_start, lower volume
    #   amix combines both
    audio_chains = [
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo[clips]",
        # atrim with explicit start; asetpts resets timestamps to 0 so
        # the subsequent afade/adelay measure from the trimmed start.
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"atrim=start={outro_trim_start:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={outro_overlap_seconds:.3f},"
        f"adelay={outro_start_ms}|{outro_start_ms},"
        f"volume={music_volume}[outro]",
        f"[clips][outro]amix=inputs=2:duration=first:"
        f"dropout_transition=0,volume={mix_volume}[aout]",
    ]

    cmd.extend([
        "-filter_complex", ";".join(audio_chains),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"overlay_music ffmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr[-1200:]}"
        )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("overlay_music produced empty output")

    return {
        "output_path": output_path,
        "duration_seconds": probe_duration(output_path),
        "size_bytes": os.path.getsize(output_path),
        "mode": f"outro overlay (last {outro_overlap_seconds}s)",
        "outro": True,
        "outro_overlap_seconds": outro_overlap_seconds,
    }


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
