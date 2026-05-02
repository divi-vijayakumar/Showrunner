"""BytePlus ARK Seedance wrapper.

Real Seedance 2.0 is async — you POST a task to `/contents/generations/tasks`
and poll until it succeeds. Takes ~60-90s per 5s clip. Model ID + URL come
from the BytePlus ARK console.

The mock provider produces a locally-generated 5s black clip so the pipeline
stays self-contained when no real API key is available.
"""
from __future__ import annotations

import asyncio
import enum
import itertools
import logging
import os
import subprocess
import threading
from typing import Any

import httpx

from ...config import settings

log = logging.getLogger(__name__)


_MOCK_DIR = "/tmp/tabloid_mock_clips"

# Round-robin pool over BYTEPLUS_API_KEYS so multi-scene dramas spread their
# create-task calls across keys. Lock-guarded — asyncio tasks run on one event
# loop in the same thread, but the celery worker may also call this from a
# different thread context, and two atomic next() calls collide cleanly.
_KEY_CYCLE = None
_KEY_LOCK = threading.Lock()


def _next_byteplus_key() -> str:
    """Return the next BytePlus key from the rotation pool, or the single
    BYTEPLUS_API_KEY when no pool is configured. Rebuilt on first use so test
    code can monkeypatch settings() before any clip is generated."""
    global _KEY_CYCLE
    pool = settings().byteplus_api_keys
    if not pool:
        return settings().byteplus_api_key
    with _KEY_LOCK:
        if _KEY_CYCLE is None:
            _KEY_CYCLE = itertools.cycle(pool)
        return next(_KEY_CYCLE)


class CameraMotion(str, enum.Enum):
    """The Tabloid camera grammar — Daily Show / The Tabloid with Veera Naatchi.

    Locked stage. Energy comes from the host's face and the writing, not the
    camera. Push for emphasis, pull for reveal/release, hold for the punchline.
    Deliberately omits orbit/tilt/crane/pan/whip — every dropped move either
    re-stages the set (orbit/tilt/crane) or fights the calm tone (whip).
    """

    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    STATIC = "static"


_MOTION_PHRASE = {
    CameraMotion.DOLLY_IN.value: "slow dolly in (push) toward the speaker",
    CameraMotion.DOLLY_OUT.value: "slow dolly out (pull) away from the subject, revealing more of the stage",
    CameraMotion.STATIC.value: "locked, no camera movement — held frame",
}


def _motion_directive(motion: str) -> str:
    """Render the camera-motion line with the seamless-cut rule baked in.

    Every clip ends on a stable held frame so cuts into the next clip don't
    strobe. Static scenes hold throughout; moving scenes decelerate into a
    held end-frame.
    """
    phrase = _MOTION_PHRASE.get(motion, motion.replace("_", " "))
    if motion == CameraMotion.STATIC.value:
        return (
            f"Camera motion: {phrase}. Held frame from start to end. "
            f"Do not re-light or re-stage."
        )
    return (
        f"Camera motion: {phrase}. Start moving at clip open, decelerate "
        f"over the final 0.8 seconds, end on a stable held frame. "
        f"Do not re-light or re-stage between this clip and any other."
    )


def _ensure_mock_clip() -> str:
    """Tiny 5s black 1080x1920 clip with SILENT audio stream — used as a
    fault-isolation placeholder when one Seedance scene fails. Must include
    an audio track because the editor's xfade pass acrossfades audio across
    every clip; a clip with no audio stream breaks the chain (exit 234)."""
    os.makedirs(_MOCK_DIR, exist_ok=True)
    path = os.path.join(_MOCK_DIR, "seedance_mock.mp4")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"file://{path}"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=0x1a1a1f:s=1080x1920:d=5:r=30",
                "-f", "lavfi", "-t", "5",
                "-i", "anullsrc=r=48000:cl=stereo",
                "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        open(path, "wb").close()
    return f"file://{path}"


def _clamp_seedance_duration(secs: int) -> int:
    """Seedance 2.0 accepts only 5, 10, 15 second clips. Pick the nearest valid."""
    if secs <= 5:
        return 5
    if secs <= 10:
        return 10
    return 15


def _auth_headers(api_key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key or settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }


async def _create_task(
    client: httpx.AsyncClient,
    prompt: str,
    *,
    duration: int,
    aspect_ratio: str,
    first_frame_image: str | None,
    last_frame_image: str | None = None,
    reference_image: str | None = None,
    reference_images: list[str] | None = None,
    generate_audio: bool = False,
    api_key: str | None = None,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    # New ARK API mutual exclusion: `last_frame image content cannot be mixed
    # with first frame or reference image content`. So we have two modes:
    #   - Endpoint-locked: first_frame + last_frame (chained-keyframe path)
    #   - Multi-reference: reference_image[] (free-floating cast/set refs)
    # If both are supplied, endpoint-locked wins because it gives stronger
    # continuity (showrunner pattern).
    use_endpoint_lock = bool(
        first_frame_image and not first_frame_image.startswith("file://")
        and last_frame_image and not last_frame_image.startswith("file://")
    )
    if use_endpoint_lock:
        content.append({
            "type": "image_url",
            "image_url": {"url": first_frame_image},
            "role": "first_frame",
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": last_frame_image},
            "role": "last_frame",
        })
    else:
        refs: list[str] = []
        if reference_images:
            refs.extend(
                r for r in reference_images if r and not r.startswith("file://")
            )
        if reference_image and not reference_image.startswith("file://"):
            refs.append(reference_image)
        if refs:
            for r in refs[:9]:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": r},
                    "role": "reference_image",
                })
        elif first_frame_image and not first_frame_image.startswith("file://"):
            content.append({
                "type": "image_url",
                "image_url": {"url": first_frame_image},
                "role": "first_frame",
            })

    payload = {
        "model": settings().seedance_model,
        "content": content,
        "ratio": aspect_ratio,
        "duration": duration,
        # When False, callers overlay Seed Speech VO in ffmpeg. When True
        # (short_drama path), Seedance produces native lip-synced audio from
        # the SPOKEN LINE block in the prompt — no separate TTS step.
        "generate_audio": generate_audio,
        "watermark": False,
    }
    base = settings().byteplus_base_url.rstrip("/")
    resp = await client.post(
        f"{base}/contents/generations/tasks",
        headers=_auth_headers(api_key),
        json=payload,
    )
    if resp.status_code >= 400:
        log.error("Seedance create %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Seedance create returned no task id: {data}")
    return task_id


async def _poll_task(
    client: httpx.AsyncClient, task_id: str, *, api_key: str | None = None
) -> dict[str, Any]:
    base = settings().byteplus_base_url.rstrip("/")
    url = f"{base}/contents/generations/tasks/{task_id}"
    # 6 min — multi-reference + generate_audio runs longer than the t2v
    # path. product-demo-studio uses 5 min for its single-image case.
    deadline = asyncio.get_event_loop().time() + 6 * 60
    delay = 3.0
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(url, headers=_auth_headers(api_key))
        if resp.status_code >= 400:
            log.error("Seedance poll %s: %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        data = resp.json()
        status = (data.get("status") or "").lower()
        if status in ("succeeded", "completed", "success"):
            return data
        if status in ("failed", "cancelled", "error"):
            err = (data.get("error") or {}).get("message") or "no detail"
            raise RuntimeError(f"Seedance task {status}: {err}")
        await asyncio.sleep(delay)
        delay = min(delay + 1.0, 8.0)
    raise TimeoutError(f"Seedance task {task_id} timed out after 4 minutes")


def _extract_video_url(task: dict[str, Any]) -> str | None:
    content = task.get("content") or {}
    output = task.get("output") or {}
    return (
        (content.get("video_url") if isinstance(content, dict) else None)
        or (output.get("video_url") if isinstance(output, dict) else None)
        or task.get("video_url")
    )


async def generate_seedance_clip(
    prompt: str,
    camera_motion: str = "dolly_in",
    duration: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
    first_frame_image: str | None = None,
    last_frame_image: str | None = None,
    reference_image: str | None = None,
    reference_images: list[str] | None = None,
    seed: int | None = None,
    end_image_url: str | None = None,
    generate_audio: bool = False,
    provider_override: str | None = None,
) -> str:
    """Generate one Seedance clip. Returns a URL to the finished mp4.

    `camera_motion` + `resolution` are passed through the prompt — Seedance
    doesn't expose separate fields for them. `first_frame_image` must be a
    public URL; file:// URLs are stripped (caller should skip img2video in
    that case). `reference_image` is the locked master-stage frame, attached
    as `role: "reference_image"` so cast/wardrobe/set stay consistent across
    every scene of the episode.
    """
    # provider_override exists so a caller can force the mock path inside a
    # real run (e.g. for a single-scene smoke test); production code leaves
    # it alone and lets the global VIDEO_PROVIDER decide.
    provider = (provider_override or settings().video_provider).lower()
    if settings().mock or provider == "mock":
        log.info(
            "MOCK Seedance request — motion=%s ratio=%s ref=%s stage=%s prompt=%s",
            camera_motion,
            aspect_ratio,
            "yes" if first_frame_image else "no",
            "yes" if reference_image else "no",
            prompt[:120],
        )
        return _ensure_mock_clip()

    # `end_image_url` is the legacy name for last_frame_image; honor either.
    last_frame_image = last_frame_image or end_image_url

    # Fold camera motion and resolution into the text prompt since ARK's
    # Seedance only takes `ratio` + `duration` + `content` natively.
    rich_prompt = (
        f"{prompt.strip()} "
        f"{_motion_directive(camera_motion)} "
        f"Resolution: {resolution}. Aspect ratio: {aspect_ratio}."
    )
    duration = _clamp_seedance_duration(duration)

    api_key = _next_byteplus_key()

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        async def _try_create(
            *, refs: bool, audio: bool
        ) -> str:
            return await _create_task(
                client,
                rich_prompt,
                duration=duration,
                aspect_ratio=aspect_ratio,
                first_frame_image=first_frame_image if refs else None,
                last_frame_image=last_frame_image if refs else None,
                reference_image=reference_image if refs else None,
                reference_images=reference_images if refs else None,
                generate_audio=audio,
                api_key=api_key,
            )

        try:
            task_id = await _try_create(refs=True, audio=generate_audio)
        except httpx.HTTPStatusError as exc:
            had_images = bool(
                reference_image or reference_images
                or first_frame_image or last_frame_image
            )
            if had_images and exc.response.status_code in (400, 422):
                log.warning(
                    "Seedance 4xx with images — retrying text-only: %s",
                    exc.response.text[:200],
                )
                task_id = await _try_create(refs=False, audio=generate_audio)
            else:
                raise
        log.info("Seedance task created: %s (audio=%s)", task_id, generate_audio)

        # Poll the task. ARK returns success/failure via the task status doc;
        # an output-audio safety failure surfaces in the failed task body, NOT
        # as an HTTP 4xx. We catch it here and retry without generate_audio
        # (per product-demo-studio's pattern) so the clip still ships, just silent.
        try:
            task = await _poll_task(client, task_id, api_key=api_key)
        except RuntimeError as exc:
            msg = str(exc)
            audio_safety = (
                "output audio may contain sensitive" in msg
                or "audio.*sensitive" in msg.lower()
            )
            if generate_audio and audio_safety:
                log.warning(
                    "Seedance output-audio flagged sensitive — retrying without audio"
                )
                retry_id = await _try_create(refs=True, audio=False)
                task = await _poll_task(client, retry_id, api_key=api_key)
            else:
                raise

    video_url = _extract_video_url(task)
    if not video_url:
        raise RuntimeError(f"Seedance returned no video URL: {task}")
    return video_url


def extract_last_frame(mp4_path: str, out_png_path: str) -> str | None:
    """Pull the final frame of a clip as a PNG so the next clip can chain
    from it as `first_frame`. Returns the path on success, None on failure
    (caller should fall back to a Seedream MCU derivative).
    """
    os.makedirs(os.path.dirname(out_png_path), exist_ok=True)
    try:
        # -sseof -0.1 seeks to 0.1s before EOF; -vframes 1 grabs one frame.
        # -update 1 keeps ffmpeg from treating the path as a numbered sequence.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-sseof", "-0.1",
                "-i", mp4_path,
                "-vframes", "1",
                "-q:v", "2",
                "-update", "1",
                out_png_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("extract_last_frame failed for %s: %s", mp4_path, exc)
        return None
    if not os.path.exists(out_png_path) or os.path.getsize(out_png_path) == 0:
        return None
    return out_png_path
