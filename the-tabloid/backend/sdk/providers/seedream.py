"""BytePlus Seedream wrapper — generate one photorealistic reference portrait
per persona, cache by persona id so we don't pay twice for the same face.

The portraits are what we pass to Seedance 2.0 as `first_frame_image` when
generating each scene, so the same "Kavitha" looks like the same person across
all 7 scenes in the segment. This is the single biggest quality lever for the
broadcast feel.

All images are produced via BytePlus ARK directly. The mock provider is the
only non-byteplus branch — used for offline tests where no real API is hit.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

import httpx

from ...config import settings

log = logging.getLogger(__name__)

_CACHE_DIR = "/tmp/tabloid_persona_portraits"
_STAGE_CACHE_DIR = "/tmp/tabloid_stage_frames"


def _portrait_cache_path(persona_id: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{persona_id}.jpg")


def _stage_cache_path(key: str) -> str:
    os.makedirs(_STAGE_CACHE_DIR, exist_ok=True)
    return os.path.join(_STAGE_CACHE_DIR, f"{key}.jpg")


def _panel_composition_key(channel_id: str, personas: list[dict[str, Any]]) -> str:
    """Cache key for stage assets — busts when the panel composition changes
    (e.g. swapped guests for a channel) but not on whitespace edits."""
    sorted_ids = "|".join(sorted(p.get("id", "") for p in personas))
    basis = f"{channel_id}|{sorted_ids}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _portrait_prompt(persona: dict[str, Any]) -> str:
    """Build a tight, repeatable description — anything vague here is a face
    that will drift between scenes.

    Important: the portrait is intentionally STYLIZED, not photoreal.
    Reasons:
      1. Seedance 2.0's i2v moderator rejects images flagged as "likeness
         of a real person" — photoreal portraits trigger this.
      2. Stylized characters drift less between Seedance img2video scenes
         than photoreal faces.
      3. The Tabloid's editorial tone fits a bold graphic-novel /
         editorial-illustration aesthetic anyway.
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
        f"3D-ANIMATED CHARACTER PORTRAIT in the style of modern Pixar / "
        f"DreamWorks / Sony Animation key art. This is a fictional cartoon "
        f"character — NOT a photograph, NOT photorealistic, NOT a real person. "
        f"Exaggerated stylized features: slightly oversized eyes, smooth "
        f"polygonal skin shading, soft cel-shaded look, unmistakably a 3D "
        f"animated film character. "
        f"Character: {name}, a fictional {role} for a satirical news debate show. "
        f"Cultural cues (clothing + setting only, NOT realistic facial features): "
        f"{culture}. Personality: {style}. "
        f"Wardrobe + lighting: {role_hint}. "
        f"Head-and-shoulders framing, direct eye line, 9:16 vertical, bold "
        f"saturated cinematic color, hand-painted texture detail. "
        f"Background: simple broadcast-set gradient, subtle THE TABLOID glow, "
        f"shallow depth of field. No on-screen text or logos. "
        f"This is animation, not photography."
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

    is_mock = settings().mock or settings().image_provider == "mock"

    # Cache lookup. Only trust the URL cache when the pipeline wants a public
    # URL (live mode) — never return a stale file:// path from a previous
    # mock run, because ARK Seedance can't fetch local files.
    if os.path.exists(url_cache_path):
        cached = open(url_cache_path).read().strip()
        if cached and (is_mock or not cached.startswith("file://")):
            return cached
    if is_mock and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return f"file://{local_path}"

    if is_mock:
        return _mock_portrait(persona, local_path)

    prompt = _portrait_prompt(persona)

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


def _panel_descriptor(personas: list[dict[str, Any]]) -> str:
    """One-line-per-panelist fingerprint, baked into both master stage and
    per-panelist MCU prompts so wardrobe + role + cultural register travel
    consistently across every Seedream call."""
    lines = []
    for p in personas:
        voice = p.get("voice") or {}
        lines.append(
            f"  · {p.get('name','?')} ({p.get('role','?')}, "
            f"{voice.get('gender','')}): {p.get('culture','')}. "
            f"Wardrobe + bearing: {p.get('style','')}"
        )
    return "\n".join(lines)


def _master_stage_prompt(personas: list[dict[str, Any]]) -> str:
    """Locked establishing shot of THE TABLOID set — 4 panelists at the desk,
    same stylized 3D-animated aesthetic as the persona portraits.

    This image is the source of truth for set/cast/wardrobe/lighting across
    every scene. Same aesthetic as the portraits avoids Seedance's i2v
    real-likeness filter and keeps drift between Seedream and Seedance minimal.
    """
    panel = _panel_descriptor(personas)
    return (
        f"3D-ANIMATED ESTABLISHING SHOT in the style of modern Pixar / "
        f"DreamWorks / Sony Animation key art. This is a fictional cartoon "
        f"scene — NOT a photograph, NOT photorealistic, NOT real people. "
        f"Exaggerated stylized features, smooth polygonal skin shading, "
        f"soft cel-shaded look, unmistakably 3D animated film characters. "
        f"\n\n"
        f"SCENE: The locked broadcast set of THE TABLOID, a satirical news "
        f"debate show. A modern curved anchor desk takes the center of the "
        f"frame. EXACTLY FOUR PANELISTS — count them: ONE, TWO, THREE, FOUR. "
        f"NOT three, NOT five, NOT six. ONLY four people at the desk, period. "
        f"They sit in a single row behind the desk, evenly spaced, facing "
        f"the camera. Behind them: out-of-focus broadcast screens and a "
        f"subtle THE TABLOID glow on the back wall. Strong key light from "
        f"camera-left, soft fill from camera-right, dark studio background. "
        f"DO NOT add a fifth panelist. DO NOT add a moderator off to the "
        f"side. DO NOT add background staff or audience. The four named "
        f"panelists below are the ONLY people in this image.\n\n"
        f"PANEL (left-to-right at the desk — EXACTLY these four, no more, no less):\n{panel}\n\n"
        f"FRAMING: Wide establishing shot — all FOUR (and only four) panelists "
        f"fully visible behind the desk, head-to-mid-torso. Symmetrical, balanced, "
        f"broadcast-polished. Composed, intelligent gazes; no smiles in resting "
        f"frame — strategic, not theatrical. 9:16 vertical composition. "
        f"Bold saturated cinematic color, hand-painted texture detail. "
        f"No on-screen text or logos. This is animation, not photography. "
        f"FINAL CHECK: count the people in your generated image — must be EXACTLY 4."
    )


def _panelist_mcu_prompt(
    panelist: dict[str, Any], all_personas: list[dict[str, Any]]
) -> str:
    """Medium close-up of one panelist seated at THE TABLOID desk. Same
    stylized aesthetic + same lighting as the master stage so cuts between
    them feel like coverage of one set, not two different renders."""
    panel = _panel_descriptor(all_personas)
    name = panelist.get("name", "?")
    role = panelist.get("role", "?")
    return (
        f"3D-ANIMATED MEDIUM CLOSE-UP in the style of modern Pixar / "
        f"DreamWorks / Sony Animation key art. This is a fictional cartoon "
        f"character — NOT a photograph, NOT photorealistic, NOT a real person. "
        f"Same stylized aesthetic, lighting, and set as the locked master "
        f"stage of THE TABLOID news debate show.\n\n"
        f"SUBJECT: {name} ({role}) seated behind the curved anchor desk of "
        f"THE TABLOID. Their face and upper torso fill the frame, with their "
        f"hands and the desk edge visible. The other three panelists remain "
        f"seated at the same desk in soft over-the-shoulder background — "
        f"out-of-focus but visibly present, not removed.\n\n"
        f"FULL PANEL CONTEXT (so the off-frame panelists are positioned "
        f"correctly):\n{panel}\n\n"
        f"LIGHTING + BACKDROP: same as the master stage — strong key light "
        f"from camera-left, soft fill, dark studio background, out-of-focus "
        f"broadcast screens, subtle THE TABLOID glow on the back wall. "
        f"\n\n"
        f"FRAMING: medium close-up on {name}, eye-line locked to camera, "
        f"composed posture. 9:16 vertical. Bold saturated cinematic color, "
        f"hand-painted texture detail. No on-screen text or logos. "
        f"This is animation, not photography."
    )


async def _seedream_request(prompt: str) -> str:
    """Shared Seedream call — used by master stage + MCU derivative paths.

    Mirrors the call shape in `generate_persona_portrait`. Returns the URL
    Seedream returned (the ARK CDN URL is stable for the segment run).
    """
    if settings().mock or settings().image_provider == "mock":
        # Mock path — caller handles fallback to a tinted PNG since stage
        # frames don't have a single persona to color-key off of.
        raise RuntimeError("mock mode — caller should use a stub image")

    payload: dict[str, Any] = {
        "model": settings().seedream_model,
        "prompt": prompt,
        "size": "1024x1820",
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
    return img_url


async def ensure_master_stage_frame(
    channel_id: str, personas: list[dict[str, Any]]
) -> str | None:
    """Generate (or reuse) the locked master stage wide for this channel +
    panel composition. Returns a public URL, or None in mock mode where we
    can't synthesize a meaningful 4-panelist establishing shot."""
    key = f"stage_{channel_id}_{_panel_composition_key(channel_id, personas)}"
    url_cache_path = _stage_cache_path(key) + ".url.txt"

    is_mock = settings().mock or settings().image_provider == "mock"
    if os.path.exists(url_cache_path):
        cached = open(url_cache_path).read().strip()
        if cached and (is_mock or not cached.startswith("file://")):
            return cached
    if is_mock:
        return None  # pipeline falls back to per-persona portraits

    prompt = _master_stage_prompt(personas)
    img_url = await _seedream_request(prompt)
    with open(url_cache_path, "w") as f:
        f.write(img_url)
    return img_url


async def ensure_panelist_mcus(
    channel_id: str, personas: list[dict[str, Any]]
) -> dict[str, str]:
    """Generate per-panelist MCU derivatives — one per persona, all rendered
    on the same locked stage so cuts between them stay consistent. Cached
    per (channel, panel composition, persona). Returns {persona_id: url}.

    Run in parallel; if any one fails the others still return."""
    is_mock = settings().mock or settings().image_provider == "mock"
    if is_mock:
        return {}

    panel_key = _panel_composition_key(channel_id, personas)

    async def _one(p: dict[str, Any]) -> tuple[str, str | None]:
        pid = p.get("id", "")
        key = f"mcu_{channel_id}_{panel_key}_{pid}"
        url_cache_path = _stage_cache_path(key) + ".url.txt"
        if os.path.exists(url_cache_path):
            cached = open(url_cache_path).read().strip()
            if cached and not cached.startswith("file://"):
                return pid, cached
        try:
            img_url = await _seedream_request(_panelist_mcu_prompt(p, personas))
        except Exception as exc:
            log.warning("MCU generation failed for %s: %s", pid, exc)
            return pid, None
        with open(url_cache_path, "w") as f:
            f.write(img_url)
        return pid, img_url

    pairs = await asyncio.gather(*[_one(p) for p in personas])
    return {pid: url for pid, url in pairs if url}


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
