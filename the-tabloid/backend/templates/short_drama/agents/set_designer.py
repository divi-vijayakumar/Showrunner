"""Set anchors for short_drama.

Two paths, both Fal-free (per product-demo-studio pattern):

  - Uploaded set photos: read from disk → base64 data URL (Seedance embeds it
    inline as reference_image). Pixar re-rendering happens via the prompt's
    STYLE LOCK block — no separate i2i call.
  - Sets the writer flagged as `__generate__`: ARK Seedream t2i. Returns a
    public ARK CDN URL.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

import httpx

from ....config import settings
from .stylize import pixarify_one as _encode_image_to_data_url

log = logging.getLogger(__name__)

_SET_CACHE_DIR = "/tmp/tabloid_drama_sets"


def _set_cache_path(set_id: str, prompt_hash: str) -> str:
    os.makedirs(_SET_CACHE_DIR, exist_ok=True)
    return os.path.join(_SET_CACHE_DIR, f"{set_id}_{prompt_hash}.url.txt")


def _set_pixar_t2i_prompt(description: str) -> str:
    return (
        "3D-ANIMATED ESTABLISHING SHOT in the style of modern Sony Pictures "
        "Animation / Pixar / DreamWorks key art. NOT a photograph. NOT "
        "photorealistic. Smooth polygonal surfaces, soft cel-shaded look, "
        "hand-painted texture detail, bold saturated cinematic color, soft "
        f"directional sunlight or appropriate ambient lighting. SCENE: {description.strip()}. "
        "No people in frame (this is a location plate — characters will be "
        "composited later). 9:16 vertical composition. Clean broadcast-quality "
        "framing, subtle depth of field. No on-screen text or logos."
    )


async def _ark_seedream_t2i(prompt: str) -> str:
    """ARK Seedream t2i. Mirrors product-demo-studio's call shape."""
    if not settings().byteplus_api_key:
        raise RuntimeError("set_designer: BYTEPLUS_API_KEY not configured")
    payload: dict[str, Any] = {
        "model": settings().seedream_model,
        "prompt": prompt,
        # ARK Seedream requires width*height >= 3,686,400 pixels.
        # 1536*2730 = 4,193,280 — comfortably above the floor and still 9:16.
        "size": "1536x2730",
        "response_format": "url",
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings().seedream_base_url.rstrip('/')}/images/generations"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("Seedream set-t2i %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        data = resp.json()
    arr = data.get("data") or data.get("images") or []
    if arr and isinstance(arr, list):
        first = arr[0]
        img = first.get("url") or first.get("image_url") or ""
        if img:
            return img
    raise RuntimeError(f"Seedream t2i returned no image URL: {data}")


_SET_SENSITIVE_KEYWORDS = (
    "tamilnadu", "tamil nadu", "parliament", "assembly", "tvk", "dmk",
    "election", "campaign", "rally",
)


def _scrub_set_desc(text: str) -> str:
    out = text
    for kw in _SET_SENSITIVE_KEYWORDS:
        out = out.replace(kw, "").replace(kw.title(), "").replace(kw.upper(), "")
    return " ".join(out.split()) or "an Indian government building exterior"


async def generate_set_master(set_id: str, description: str) -> str:
    """Generate a Pixar-style set master from a text description. Cached.

    Two-pass: first with the natural description, on
    OutputImageSensitiveContentDetected retry with a sanitized version
    (strips political-rally keywords)."""
    h = hashlib.sha1(description.encode("utf-8")).hexdigest()[:10]
    cache_path = _set_cache_path(set_id, h)
    if os.path.exists(cache_path):
        cached = open(cache_path).read().strip()
        if cached.startswith("http"):
            log.info("set master cache hit for %s", set_id)
            return cached

    prompt = _set_pixar_t2i_prompt(description)
    try:
        img_url = await _ark_seedream_t2i(prompt)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        if "OutputImageSensitiveContent" in body or "InputImageSensitiveContent" in body:
            log.warning(
                "set Seedream sensitivity-flagged %s — retrying sanitized",
                set_id,
            )
            sanitized = _set_pixar_t2i_prompt(_scrub_set_desc(description))
            img_url = await _ark_seedream_t2i(sanitized)
        else:
            raise
    with open(cache_path, "w") as f:
        f.write(img_url)
    log.info("set master generated for %s → %s", set_id, img_url[:80])
    return img_url


async def pixarify_uploaded_set(
    asset_id: str, label: str, description: str = "", local_path: str = ""
) -> str:
    """Pixarify an uploaded location photo via ARK Seedream i2i — preserves
    the actual location's composition while re-rendering Pixar-style. Falls
    back to t2i from the description if the i2i trips the moderator."""
    desc = description.strip() or label.replace("_", " ")
    if local_path and os.path.exists(local_path):
        # i2i path — reuse the stylize module's helpers (same `image` field
        # support on /images/generations).
        from .stylize import _file_to_data_url, _ark_seedream as _seedream  # type: ignore
        h = hashlib.sha1(f"{local_path}|{desc}".encode("utf-8")).hexdigest()[:10]
        cache_path = _set_cache_path(f"uploaded_{asset_id}", f"{h}_i2i")
        if os.path.exists(cache_path):
            cached = open(cache_path).read().strip()
            if cached.startswith("http"):
                log.info("uploaded set i2i cache hit for %s", asset_id)
                return cached
        try:
            data_url = _file_to_data_url(local_path)
            prompt = (
                "Re-imagine this location as a 3D-animated cartoon set in "
                "the exaggerated style of a Disney/Pixar/DreamWorks movie "
                "background plate. NOT a photograph. Heavily stylized, "
                "smooth polygonal surfaces, soft cel-shaded look, hand-"
                "painted texture detail, bold saturated cinematic color. "
                f"Location context: {desc}. Empty of people. 9:16 vertical."
            )
            img_url = await _seedream(prompt, image=data_url)
            with open(cache_path, "w") as f:
                f.write(img_url)
            log.info("set i2i Pixarified %s → %s", asset_id, img_url[:80])
            return img_url
        except httpx.HTTPStatusError as exc:
            body = exc.response.text or ""
            if "OutputImageSensitive" in body or "InputImageSensitive" in body:
                log.warning("set i2i flagged %s — falling back to t2i", asset_id)
            else:
                raise
    return await generate_set_master(f"uploaded_{asset_id}", desc)


async def resolve_all_sets(
    uploaded_sets: list[dict[str, Any]],
    sets_to_generate: list[dict[str, Any]],
) -> dict[str, str]:
    """Returns {set_label_or_id: ARK_CDN_url}.

    Both uploaded and writer-flagged sets get the same treatment: ARK Seedream
    t2i generates a Pixar-style master from the description. Uploaded photos
    are NOT used as first_frame because Seedance follows photoreal references
    over the Pixar prompt and produces a photoreal clip."""

    async def _upload_one(s: dict[str, Any]) -> tuple[str, str | None]:
        try:
            url = await pixarify_uploaded_set(
                s["id"], s["label"], s.get("description", ""),
                local_path=s.get("local_path", ""),
            )
            return s["label"], url
        except Exception as exc:
            log.warning("set Pixarify for upload failed (%r): %s", s.get("label"), exc)
            return s["label"], None

    async def _gen_one(s: dict[str, Any]) -> tuple[str, str | None]:
        try:
            url = await generate_set_master(s["id"], s["description"])
            return s["id"], url
        except Exception as exc:
            log.warning("set t2i failed for %r: %s", s.get("id"), exc)
            return s["id"], None

    tasks: list = []
    tasks.extend([_upload_one(s) for s in uploaded_sets])
    tasks.extend([_gen_one(s) for s in sets_to_generate])
    if not tasks:
        return {}
    pairs = await asyncio.gather(*tasks)
    return {k: v for k, v in pairs if v}
