"""BytePlus Seedance 2.0 T2V wrapper.

Submit → poll → return final video URL. In mock mode returns a placeholder URL.
Seedance is QPS-limited (2 rps, 3 concurrent) — callers should sequence requests.
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
    """Generate a tiny 5s black 1080x1920 clip once, reuse on subsequent calls."""
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


async def generate_seedance_clip(
    prompt: str,
    camera_motion: str = "dolly_in",
    duration: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
    model: str = "seedance-2.0",
    first_frame_image: str | None = None,
) -> str:
    """Submit a T2V (or img2video, if `first_frame_image` is a URL) job and
    poll until complete. Returns video URL.
    """
    if settings().mock:
        log.info(
            "MOCK Seedance request — model=%s motion=%s ratio=%s ref=%s prompt=%s",
            model,
            camera_motion,
            aspect_ratio,
            "yes" if first_frame_image else "no",
            prompt[:120],
        )
        return _ensure_mock_clip()

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "style": "cinematic",
        "camera_motion": camera_motion,
        "audio": False,
    }
    if first_frame_image:
        # Seedance img2video entry point. If the model tier doesn't accept a
        # reference frame the API will 400 — caller should then retry without.
        payload["first_frame_image"] = first_frame_image
    headers = {
        "Authorization": f"Bearer {settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }
    base = settings().byteplus_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(f"{base}/videos", headers=headers, json=payload)
        resp.raise_for_status()
        job_id = resp.json()["job_id"]
        log.info("Seedance job submitted id=%s", job_id)

        # Poll. Seedance takes ~60-90s at 1080p.
        for _ in range(60):  # 60 * 5s = 5 minutes max
            await asyncio.sleep(5)
            r = await client.get(f"{base}/videos/{job_id}", headers=headers)
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "completed":
                return data["output"]["video_url"]
            if status == "failed":
                raise RuntimeError(f"Seedance job failed: {data.get('error')}")
        raise TimeoutError(f"Seedance job {job_id} timed out after 5 minutes")


# Reference: camera_motion values supported by Seedance
# dolly_in / dolly_out   — push toward / pull away from subject
# pan_left / pan_right   — horizontal sweep
# tilt_up / tilt_down    — vertical tilt
# orbit_left / orbit_right — orbit around subject
# crane_up / crane_down  — crane movement
# zoom_in / zoom_out     — optical zoom
# static                 — no camera movement (for data/graphic scenes)
