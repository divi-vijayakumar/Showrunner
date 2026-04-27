"""BytePlus ARK Seedance wrapper.

Real Seedance 2.0 is async — you POST a task to `/contents/generations/tasks`
and poll until it succeeds. Takes ~60-90s per 5s clip. Model ID + URL come
from the BytePlus ARK console.

In mock mode we return a locally-generated 5s black clip so the pipeline
stays self-contained.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import os
import subprocess
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


_MOCK_DIR = "/tmp/tabloid_mock_clips"


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
    reference_image: str | None = None,
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
    if reference_image and not reference_image.startswith("file://"):
        # Master-stage subject reference. Seedance 2.0 is documented to accept
        # `first_frame` + `reference_image` in the same request — the reference
        # locks cast/wardrobe/set across all 7 scenes while the first_frame
        # pins each clip's opening composition. If the endpoint silently
        # ignores this role we still get the first_frame pin (no harm). If it
        # 422s we fall back at the call site.
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": reference_image},
                "role": "reference_image",
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
    reference_image: str | None = None,
    seed: int | None = None,
    model_override: str | None = None,
    end_image_url: str | None = None,
) -> str:
    """Generate one Seedance clip. Returns a URL to the finished mp4.

    `camera_motion` + `resolution` are passed through the prompt — Seedance
    doesn't expose separate fields for them. `first_frame_image` must be a
    public URL; file:// URLs are stripped (caller should skip img2video in
    that case). `reference_image` is the locked master-stage frame, attached
    as `role: "reference_image"` so cast/wardrobe/set stay consistent across
    every scene of the episode.
    """
    provider = settings().video_provider
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
    if provider == "fal":
        from .fal import generate_fal_video
        # Bake camera + composition into the prompt since the Fal models
        # don't expose a separate camera-motion field.
        rich_prompt = (
            f"{prompt.strip()} "
            f"{_motion_directive(camera_motion)} "
            f"Aspect ratio: {aspect_ratio}. Broadcast-polished composition."
        )
        return await generate_fal_video(
            rich_prompt,
            duration_s=duration,
            first_frame_image=first_frame_image,
            seed=seed,
            aspect_ratio=aspect_ratio,
            model_override=model_override,
            end_image_url=end_image_url,
        )

    # Fold camera motion and resolution into the text prompt since ARK's
    # Seedance only takes `ratio` + `duration` + `content` natively.
    rich_prompt = (
        f"{prompt.strip()} "
        f"{_motion_directive(camera_motion)} "
        f"Resolution: {resolution}. Aspect ratio: {aspect_ratio}."
    )
    duration = _clamp_seedance_duration(duration)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            task_id = await _create_task(
                client,
                rich_prompt,
                duration=duration,
                aspect_ratio=aspect_ratio,
                first_frame_image=first_frame_image,
                reference_image=reference_image,
            )
        except httpx.HTTPStatusError as exc:
            # If BytePlus rejects the dual-image payload (unknown role), retry
            # with first_frame only. Cheaper than failing the clip outright.
            if reference_image and exc.response.status_code in (400, 422):
                log.warning(
                    "Seedance 422 with reference_image — retrying without it: %s",
                    exc.response.text[:200],
                )
                task_id = await _create_task(
                    client,
                    rich_prompt,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    first_frame_image=first_frame_image,
                    reference_image=None,
                )
            else:
                raise
        log.info("Seedance task created: %s", task_id)
        task = await _poll_task(client, task_id)

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
