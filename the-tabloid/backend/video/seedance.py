"""BytePlus ARK Seedance wrapper.

Real Seedance 2.0 is async — you POST a task to `/contents/generations/tasks`
and poll until it succeeds. Takes ~60-90s per 5s clip. Model ID + URL come
from the BytePlus ARK console.

In mock mode we return a locally-generated 5s black clip so the pipeline
stays self-contained.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


_MOCK_DIR = "/tmp/tabloid_mock_clips"


def _ensure_mock_clip() -> str:
    """Tiny 5s black 1080x1920 clip for offline dev, generated once."""
    os.makedirs(_MOCK_DIR, exist_ok=True)
    path = os.path.join(_MOCK_DIR, "seedance_mock.mp4")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"file://{path}"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=0x1a1a1f:s=1080x1920:d=5:r=30",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
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


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }


async def _create_task(
    client: httpx.AsyncClient,
    prompt: str,
    *,
    duration: int,
    aspect_ratio: str,
    first_frame_image: str | None,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if first_frame_image and not first_frame_image.startswith("file://"):
        # Seedance fetches the image server-side, so it must be a public URL.
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": first_frame_image},
                "role": "first_frame",
            }
        )

    payload = {
        "model": settings().seedance_model,
        "content": content,
        "ratio": aspect_ratio,
        "duration": duration,
        "generate_audio": False,  # we overlay Seed Speech VO in ffmpeg
        "watermark": False,
    }
    base = settings().byteplus_base_url.rstrip("/")
    resp = await client.post(
        f"{base}/contents/generations/tasks",
        headers=_auth_headers(),
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


async def _poll_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    base = settings().byteplus_base_url.rstrip("/")
    url = f"{base}/contents/generations/tasks/{task_id}"
    deadline = asyncio.get_event_loop().time() + 4 * 60  # 4 min
    delay = 3.0
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(url, headers=_auth_headers())
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
    model: str = "",
    first_frame_image: str | None = None,
) -> str:
    """Generate one Seedance clip. Returns a URL to the finished mp4.

    `camera_motion` + `resolution` are passed through the prompt — Seedance
    doesn't expose separate fields for them. `first_frame_image` must be a
    public URL; file:// URLs are stripped (caller should skip img2video in
    that case).
    """
    if settings().mock:
        log.info(
            "MOCK Seedance request — motion=%s ratio=%s ref=%s prompt=%s",
            camera_motion,
            aspect_ratio,
            "yes" if first_frame_image else "no",
            prompt[:120],
        )
        return _ensure_mock_clip()

    # Fold camera motion and resolution into the text prompt since ARK's
    # Seedance only takes `ratio` + `duration` + `content` natively.
    rich_prompt = (
        f"{prompt.strip()} "
        f"Camera motion: {camera_motion.replace('_', ' ')}. "
        f"Resolution: {resolution}. Aspect ratio: {aspect_ratio}."
    )
    duration = _clamp_seedance_duration(duration)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        task_id = await _create_task(
            client,
            rich_prompt,
            duration=duration,
            aspect_ratio=aspect_ratio,
            first_frame_image=first_frame_image,
        )
        log.info("Seedance task created: %s", task_id)
        task = await _poll_task(client, task_id)

    video_url = _extract_video_url(task)
    if not video_url:
        raise RuntimeError(f"Seedance returned no video URL: {task}")
    return video_url


# Reference: camera_motion values commonly supported by Seedance
# dolly_in / dolly_out   — push toward / pull away from subject
# pan_left / pan_right   — horizontal sweep
# tilt_up / tilt_down    — vertical tilt
# orbit_left / orbit_right — orbit around subject
# crane_up / crane_down  — crane movement
# zoom_in / zoom_out     — optical zoom
# static                 — no camera movement
