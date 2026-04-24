"""BytePlus Seedream wrapper — generate one photorealistic reference portrait
per persona, cache by persona id so we don't pay twice for the same face.

The portraits are what we pass to Seedance 2.0 as `first_frame_image` when
generating each scene, so the same "Kavitha" looks like the same person across
all 7 scenes in the segment. This is the single biggest quality lever for the
broadcast feel.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)

_CACHE_DIR = "/tmp/tabloid_persona_portraits"


def _portrait_cache_path(persona_id: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{persona_id}.jpg")


def _portrait_prompt(persona: dict[str, Any]) -> str:
    """Build a tight, repeatable description — anything vague here is a face
    that will drift between scenes.
    """
    name = persona.get("name", "")
    role = persona.get("role", "")
    culture = persona.get("culture", "")
    style = persona.get("style", "")
    voice = persona.get("voice") or {}
    gender = voice.get("gender", "")

    # Role-driven visual fingerprint — these travel into every scene prompt.
    role_hint = {
        "anchor": "composed, polished, anchor desk lighting, neat tailored blazer",
        "provocateur": "intense gaze, slight tension in the jaw, rim-lit from behind",
        "analyst": "thoughtful, restrained, subtle glasses or notebook, clean office",
        "humanist": "warm, open expression, softer ambient lighting, lived-in clothing",
    }.get(role, "")

    return (
        f"Photorealistic broadcast portrait of {name}, a {gender} {role} on a news "
        f"debate show. Cultural context: {culture}. Personality cue: {style}. "
        f"Wardrobe + lighting: {role_hint}. Head-and-shoulders, direct eye line, "
        "9:16 vertical composition, soft 50mm depth of field, skin texture visible, "
        "cinematic color grade, absolutely no text or logos in frame."
    )


def _persona_cache_key(persona: dict[str, Any]) -> str:
    """Hash the fields that matter for the image so small prompt tweaks bust
    the cache. We don't want to regenerate on whitespace changes."""
    basis = "|".join(
        str(persona.get(k, ""))
        for k in ("id", "name", "role", "culture", "style")
    ) + "|" + str((persona.get("voice") or {}).get("gender", ""))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


async def generate_persona_portrait(persona: dict[str, Any]) -> str:
    """Return a URL to the persona portrait.

    Mock mode: writes a role-tinted PNG locally and returns file:// — callers
    that need a public URL (e.g. Seedance img2video) should skip first_frame
    when they see file://.

    Live mode: asks ARK Seedream for a URL and returns it verbatim. We do
    NOT download + re-host because (a) Seedance needs a reachable URL for
    img2video, and (b) the ARK CDN URL is good for the life of a segment run.
    """
    persona_id = persona.get("id") or _persona_cache_key(persona)
    cache_key = f"{persona_id}_{_persona_cache_key(persona)}"
    local_path = _portrait_cache_path(cache_key)
    url_cache_path = local_path + ".url.txt"

    provider = settings().image_provider
    is_mock = settings().mock or provider == "mock"

    # Cache lookup. When the pipeline wants a public URL (fal or byteplus),
    # only trust the URL cache — never return a stale file:// path from a
    # previous mock run, because Fal/Seedance can't fetch local files.
    if os.path.exists(url_cache_path):
        cached = open(url_cache_path).read().strip()
        if cached and (is_mock or not cached.startswith("file://")):
            return cached
    if is_mock and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return f"file://{local_path}"

    if is_mock:
        return _mock_portrait(persona, local_path)

    prompt = _portrait_prompt(persona)

    if provider == "fal":
        from .fal import generate_fal_image
        img_url = await generate_fal_image(prompt, size="portrait_16_9")
        with open(url_cache_path, "w") as f:
            f.write(img_url)
        return img_url

    payload: dict[str, Any] = {
        "model": settings().seedream_model,
        "prompt": prompt,
        "size": "1024x1820",  # 9:16-ish; ARK accepts arbitrary sizes
        "response_format": "url",
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {settings().byteplus_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings().seedream_base_url.rstrip('/')}/images/generations"

    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("Seedream %s: %s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
        data = resp.json()

    img_url = _extract_image_url(data)
    if not img_url:
        raise RuntimeError(f"Seedream returned no image URL: {data}")

    with open(url_cache_path, "w") as f:
        f.write(img_url)
    return img_url


def _extract_image_url(data: dict[str, Any]) -> str:
    """Seedream responses carry the image URL in either data[0].url or
    data[0].b64_json depending on the tier; we tolerate both OpenAI-style
    and BytePlus-native shapes.
    """
    arr = data.get("data") or data.get("images") or []
    if arr and isinstance(arr, list):
        first = arr[0]
        return first.get("url") or first.get("image_url") or ""
    return data.get("url") or ""


async def ensure_persona_portraits(
    personas: list[dict[str, Any]],
) -> dict[str, str]:
    """Generate portraits for every persona in parallel (BytePlus rate limits
    are per-model, so this is safe). Returns {persona_id: file_url}."""
    out: dict[str, str] = {}
    tasks = [generate_persona_portrait(p) for p in personas]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for p, r in zip(personas, results):
        if isinstance(r, Exception):
            log.warning("portrait generation failed for %s: %s", p.get("id"), r)
            continue
        out[p["id"]] = r
    return out


def _mock_portrait(persona: dict[str, Any], path: str) -> str:
    """Render a deterministic colored tile with the persona's initials so mock
    runs have *something* to pass into Seedance img2video, and different
    personas look visibly different on-stage."""
    import struct, zlib
    role_tint = {
        "anchor": (99, 102, 241),      # indigo
        "provocateur": (239, 68, 68),  # red
        "analyst": (16, 185, 129),     # emerald
        "humanist": (245, 158, 11),    # amber
    }.get(persona.get("role", ""), (103, 80, 164))

    W, H = 540, 960
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        for x in range(W):
            t = y / H
            r = int(role_tint[0] * (1 - 0.6 * t))
            g = int(role_tint[1] * (1 - 0.6 * t))
            b = int(role_tint[2] * (1 - 0.6 * t))
            raw.append(r); raw.append(g); raw.append(b)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(png)
    return f"file://{path}"
