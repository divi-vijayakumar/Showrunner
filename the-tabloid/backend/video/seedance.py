"""BytePlus Seedance 2.0 T2V wrapper.

Submit → poll → return final video URL. In mock mode returns a placeholder URL.
Seedance is QPS-limited (2 rps, 3 concurrent) — callers should sequence requests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


_MOCK_CLIP_URL = "https://storage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"


async def generate_seedance_clip(
    prompt: str,
    camera_motion: str = "dolly_in",
    duration: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
    model: str = "seedance-2.0",
) -> str:
    """Submit a T2V job and poll until complete. Returns video URL."""
    if settings().mock:
        # Log what we *would* have asked Seedance to do, useful for demo prep.
        log.info(
            "MOCK Seedance request — model=%s motion=%s ratio=%s prompt=%s",
            model,
            camera_motion,
            aspect_ratio,
            prompt[:120],
        )
        return _MOCK_CLIP_URL

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
