"""Fal.ai wrappers — used as fallbacks when BytePlus Seed surfaces are blocked.

- Video: MiniMax Hailuo 02 (image-to-video) via fal.ai queue.
- Image: Flux Schnell for persona reference portraits.

Queue pattern: POST {BASE}/{model} → get `request_id` → GET {BASE}/{model}/requests/{id}/status
until `COMPLETED` → GET {BASE}/{model}/requests/{id} for the final payload. We
use https://queue.fal.run for the queue base.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)

_QUEUE_BASE = "https://queue.fal.run"


def _fal_headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {settings().fal_api_key}",
        "Content-Type": "application/json",
    }


async def _submit_and_wait(
    model: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 240.0,
    poll_every_s: float = 3.0,
) -> dict[str, Any]:
    """Submit a job to fal queue and wait for the result. Returns the final
    response body (the content depends on the model)."""
    if not settings().fal_api_key:
        raise RuntimeError("FAL_KEY is unset but provider is fal")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        # Submit
        submit_url = f"{_QUEUE_BASE}/{model}"
        resp = await client.post(submit_url, headers=_fal_headers(), json=payload)
        if resp.status_code >= 400:
            log.error("Fal submit %s at %s: %s", resp.status_code, resp.url, resp.text[:300])
        resp.raise_for_status()
        submit_data = resp.json()
        request_id = submit_data.get("request_id")
        status_url = submit_data.get("status_url") or f"{_QUEUE_BASE}/{model}/requests/{request_id}/status"
        result_url = submit_data.get("response_url") or f"{_QUEUE_BASE}/{model}/requests/{request_id}"

        # Poll
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_every_s)
            r = await client.get(status_url, headers=_fal_headers())
            r.raise_for_status()
            state = (r.json().get("status") or "").upper()
            if state == "COMPLETED":
                break
            if state in ("FAILED", "ERROR", "CANCELLED"):
                raise RuntimeError(f"Fal job {request_id} {state}: {r.text[:300]}")
        else:
            raise TimeoutError(f"Fal job {request_id} timed out after {timeout_s:.0f}s")

        # Fetch result
        r = await client.get(result_url, headers=_fal_headers())
        if r.status_code >= 400:
            log.error("Fal result %s at %s: %s", r.status_code, r.url, r.text[:500])
        r.raise_for_status()
        return r.json()


# -- Video: Hailuo 02 img2video ---------------------------------------------

async def generate_fal_video(
    prompt: str,
    *,
    duration_s: int = 5,
    first_frame_image: str | None = None,
    seed: int | None = None,
    aspect_ratio: str = "9:16",
    model_override: str | None = None,
    end_image_url: str | None = None,
) -> str:
    """Generate one clip via the configured Fal video model. Returns a public
    video URL.

    Pass `model_override` to switch t2v↔i2v per call.

    Pass `end_image_url` (alongside `first_frame_image`) for endpoint-locked
    i2v: Seedance interpolates motion between the two locked frames. Used by
    the alternating-anchor pattern where even scenes lock back to the avatar.

    Passing `seed` holds randomness stable between calls, so if you pass the
    same seed for every scene featuring the same persona, faces drift less.
    """
    model = model_override or settings().fal_video_model

    payload: dict[str, Any] = {
        "prompt": prompt,
        # Seedance accepts 5 or 10. Hailuo accepts "6" or "10" as strings.
        "duration": _duration_for_model(model, duration_s),
        "aspect_ratio": aspect_ratio,
    }
    if "seedance-2.0" in model:
        # Seedance 2.0 (fast or premium) — synchronized native audio when
        # generate_audio=true. Tops out at 720p; pricing scales linearly.
        payload["generate_audio"] = True
        payload["resolution"] = "720p"
    elif "seedance" in model:
        # Older v1 paths: silent only; resolution up to 1080p. The flag is
        # ignored by the API but we keep it so the param shape doesn't
        # split across versions.
        payload["resolution"] = "1080p"
        payload["generate_audio"] = True
    elif "hailuo" in model:
        payload["resolution"] = "768P"
        payload["prompt_optimizer"] = True
    else:
        payload["resolution"] = "1080p"
    if seed is not None:
        payload["seed"] = int(seed)

    # text-to-video paths take prompt only — never an image, even if the
    # caller passes one. img2video paths take a public-URL image (file://
    # never works because Fal fetches it server-side).
    is_t2v = "text-to-video" in model
    if not is_t2v and first_frame_image and not first_frame_image.startswith("file://"):
        payload["image_url"] = first_frame_image
    # Endpoint-locked i2v: when end_image_url is set, Seedance interpolates
    # between image_url (start) and end_image_url (end). Used by the
    # alternating-anchor pattern where even scenes lock back to the avatar.
    if not is_t2v and end_image_url and not end_image_url.startswith("file://"):
        payload["end_image_url"] = end_image_url

    data = await _submit_and_wait(model, payload, timeout_s=300.0, poll_every_s=4.0)

    # Both Seedance and Hailuo return `{ video: { url: ... } }` on Fal.
    video = data.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        # Some models return `video_url` at the top level — tolerate it.
        url = data.get("video_url")
    if not url:
        raise RuntimeError(f"Fal video returned no url: keys={list(data)[:10]}")
    return url


def _duration_for_model(model: str, requested: int) -> Any:
    """Fal models disagree on duration type + accepted values."""
    if "hailuo" in model:
        return "6" if requested <= 6 else "10"
    # Seedance accepts 5 or 10 as int.
    return 5 if requested <= 5 else 10


# -- Image: Flux Schnell portraits ------------------------------------------

async def generate_fal_image(
    prompt: str,
    *,
    size: str = "portrait_16_9",
) -> str:
    """Generate an image via Fal's Flux Schnell. Returns a public image URL."""
    model = settings().fal_image_model
    payload = {
        "prompt": prompt,
        "image_size": size,
        "num_inference_steps": 4,  # Schnell is tuned for 1-4 steps
        "num_images": 1,
        "enable_safety_checker": True,
    }

    data = await _submit_and_wait(model, payload, timeout_s=60.0, poll_every_s=2.0)

    images = data.get("images") or []
    if not images:
        raise RuntimeError(f"Fal Flux returned no images: {list(data)[:10]}")
    url = images[0].get("url") if isinstance(images[0], dict) else None
    if not url:
        raise RuntimeError(f"Fal Flux image shape unexpected: {images[0]}")
    return url
