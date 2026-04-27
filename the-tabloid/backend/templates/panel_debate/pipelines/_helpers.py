"""Shared helpers used by every panel-debate pipeline mode.

Anything that's NOT mode-specific lives here:
  - the segment data layout (SEGMENTS_DIR, manifest read/write)
  - the chain-anchor + last-frame ffmpeg helpers
  - the avatar→Fal upload-and-cache helper
  - the per-scene clip-state initial shape + local-mp4 download helper
  - cost / model / speaker-visual-id constants the prompt builders share

Mode-specific logic — RSS pulling, debate simulation, the prompt
construction with the alternating-anchor pattern, etc. — stays in
`full.py` / `direct.py` / `sample.py`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

from ....config import settings

log = logging.getLogger(__name__)


# Where direct-mode segments live on disk. Resolved relative to repo root
# so it survives uvicorn reloads + uvicorn started from any cwd.
SEGMENTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "segments")
)


# Seedance fast-tier endpoints — direct-mode uses i2v almost exclusively;
# t2v is the avatar-failed fallback for scene 1 only.
_T2V_MODEL = "bytedance/seedance-2.0/fast/text-to-video"
_I2V_MODEL = "bytedance/seedance-2.0/fast/image-to-video"


# Short distinctive visual identifier per panelist. Fed into the lean i2v
# prompt so the model can locate the speaker in the chain anchor frame by
# wardrobe color / glasses / etc — using "center-left" alone wasn't enough
# (model defaulted to whoever was previously featured). Hardcoded for the
# Skyroot panel; for other channels, extend or pull from visual_description.
_SPEAKER_VISUAL_ID = {
    "veera_naatchi": (
        "shoulder-length wavy dark brown hair with a side parting, "
        "dark navy blazer over a crisp white shirt, subtle natural "
        "makeup, soft composed expression"
    ),
    "amit_sharma": (
        "dark charcoal western suit with a white shirt and dark tie, "
        "dark short hair, no beard, clean-shaven"
    ),
    "arjun_rao": (
        "full dark beard, dark hair, dark square-rimmed glasses, "
        "charcoal-grey blazer over a white shirt"
    ),
    "anjali_mehta": (
        "deep maroon-red sari with a darker patterned border, gold "
        "dangling earrings, small dark bindi on the forehead, long "
        "dark hair"
    ),
}


# Estimated cost per Fal call (USD). Used for live cost tracking in the
# manifest + player UI. Fal's published rates April 2026:
#   - Seedance 2.0 fast (t2v + i2v): $0.2419/s × 5s = $1.21/clip
# Failed scenes don't bill (Fal rejects 422/timeout pre-completion).
_COST_T2V = 1.21
_COST_I2V = 1.21
_COST_BY_MODE = {
    # t2v: text-to-video, no input image. Used only as fallback when no
    # avatar exists (effectively never on direct mode).
    "t2v": _COST_T2V,
    # i2v: i2v with image_url only (no end frame). Generic fallback.
    "i2v": _COST_I2V,
    # i2v-avatar: scene 1 path. image_url = avatar PNG (free landing).
    "i2v-avatar": _COST_I2V,
    # i2v-chained: odd scenes 3+. image_url = prior scene's last frame
    # (which IS the avatar because prior even scene end-anchored to it).
    # No end_image_url — free landing for the speaker's beat.
    "i2v-chained": _COST_I2V,
    # i2v-locked: even scenes 2+. image_url = prior scene's last frame +
    # end_image_url = avatar — re-anchors panel composition every cut.
    # Same Fal endpoint as i2v-chained, just with the second image.
    "i2v-locked": _COST_I2V,
}


def _scene_slug(scene_number: Any) -> str:
    """Filename-safe scene identifier. Pads ints to 2 digits ("09"), keeps
    strings as-is ("9b"). Used for `scene_NN.mp4` and chain-anchor PNG
    filenames + the `/api/segments/{sid}/scene/{N}.mp4` URL path. Lets us
    insert sub-scenes (e.g. "9b" between scene 9 and scene 10) without
    renaming or overwriting existing files."""
    try:
        return f"{int(scene_number):02d}"
    except (ValueError, TypeError):
        return str(scene_number)


def _segment_dir(segment_id: str) -> str:
    p = os.path.join(SEGMENTS_DIR, segment_id)
    os.makedirs(p, exist_ok=True)
    return p


def _manifest_path(segment_id: str) -> str:
    return os.path.join(_segment_dir(segment_id), "manifest.json")


def load_segment_manifest(segment_id: str) -> dict[str, Any] | None:
    """Read the on-disk manifest for a direct-mode segment. Used by both
    the pipeline (to resume a batched run) and the API (to serve clips
    after uvicorn restarts that wipe in-memory mock state)."""
    import json
    path = _manifest_path(segment_id)
    if not os.path.exists(path):
        return None
    try:
        return json.loads(open(path).read())
    except Exception as exc:
        log.warning("manifest read failed for %s: %s", segment_id, exc)
        return None


def _write_segment_manifest(segment_id: str, data: dict[str, Any]) -> None:
    import json, time
    data = dict(data)
    data["updated_at"] = time.time()
    data.setdefault("created_at", data["updated_at"])
    path = _manifest_path(segment_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _extract_last_frame_to_png(mp4_path: str, png_path: str) -> str | None:
    """ffmpeg: grab the final frame as a PNG. Returns path or None on failure."""
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-sseof", "-0.1", "-i", mp4_path,
                "-vframes", "1", "-q:v", "2", "-update", "1", png_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log.warning("last-frame extract failed for %s: %s", mp4_path, exc)
        return None
    if not os.path.exists(png_path) or os.path.getsize(png_path) == 0:
        return None
    return png_path


async def _resolve_local_avatar(avatar_path: str) -> str | None:
    """Take a local image path (relative to repo root or absolute), upload
    it to Fal storage on first call, cache the public URL in a sidecar file
    next to the image. Subsequent calls reuse the cached URL with no
    re-upload. Returns None on any failure.
    """
    if not avatar_path:
        return None
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    abs_path = (
        avatar_path if os.path.isabs(avatar_path)
        else os.path.join(repo_root, avatar_path)
    )
    if not os.path.exists(abs_path):
        log.warning("avatar_path does not exist: %s", abs_path)
        return None

    cache_path = abs_path + ".fal_url.txt"
    if os.path.exists(cache_path):
        try:
            cached = open(cache_path).read().strip()
            if cached.startswith("http"):
                return cached
        except Exception:
            pass

    try:
        os.environ.setdefault("FAL_KEY", settings().fal_api_key)
        import fal_client
    except Exception as exc:
        log.warning("fal_client unavailable for avatar upload: %s", exc)
        return None

    try:
        url = await asyncio.to_thread(fal_client.upload_file, abs_path)
    except Exception as exc:
        log.warning("avatar upload failed: %s", exc)
        return None

    try:
        with open(cache_path, "w") as f:
            f.write(url)
    except Exception:
        pass
    return url


async def _prep_chain_anchor(
    segment_id: str, scene_number: Any, local_mp4: str
) -> str | None:
    """For scene N → produce the chain anchor URL that scene N+1 will pass
    as first_frame for i2v. Stylized-aesthetic episodes don't need
    face-stripping — Fal's i2v moderator only flags photoreal facial
    likeness, not stylized cartoon output. So we just extract the last
    frame and upload it raw. Returns None on any failure (caller halts).
    """
    if not local_mp4 or not os.path.exists(local_mp4):
        return None
    try:
        os.environ.setdefault("FAL_KEY", settings().fal_api_key)
        import fal_client
    except Exception as exc:
        log.warning("fal_client unavailable for chain prep: %s", exc)
        return None

    png_path = os.path.join(
        _segment_dir(segment_id),
        f"chain_anchor_after_scene_{_scene_slug(scene_number)}.png",
    )
    if not _extract_last_frame_to_png(local_mp4, png_path):
        return None

    try:
        anchor_url = await asyncio.to_thread(fal_client.upload_file, png_path)
    except Exception as exc:
        log.warning("chain anchor upload failed: %s", exc)
        return None
    return anchor_url


async def _save_clip_locally(
    segment_id: str, scene_number: Any, remote_url: str
) -> tuple[str, str] | tuple[None, None]:
    """Download a Fal CDN mp4 to data/segments/{sid}/scene_NN.mp4 and return
    (public_url, local_path). Returns (None, None) on failure.
    """
    from ....sdk.providers.ffmpeg import download_file
    slug = _scene_slug(scene_number)
    local_name = f"scene_{slug}.mp4"
    local_path = os.path.join(_segment_dir(segment_id), local_name)
    try:
        await download_file(remote_url, local_path)
    except Exception as exc:
        log.warning(
            "scene %s download failed for %s: %s",
            scene_number, segment_id, str(exc)[:200],
        )
        return None, None
    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        return None, None
    base = settings().public_base_url.rstrip("/")
    public_url = f"{base}/api/segments/{segment_id}/scene/{slug}.mp4"
    return public_url, local_path


def _initial_clips_state(
    scenes: list[dict[str, Any]],
    personas_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the full N-entry clips array with every scene marked 'queued'.
    Batched runs only flip a slice of these to 'pending' / 'ready' / 'failed'.
    """
    out: list[dict[str, Any]] = []
    for i, s in enumerate(scenes):
        speaker = (
            personas_by_id.get(s.get("featured_persona_id"), {})
            .get("name", s.get("featured_role", "?"))
        )
        out.append({
            "scene_number": s.get("scene_number", i + 1),
            "speaker": speaker,
            "line": s.get("vo_line", ""),
            "video_url": None,
            "status": "queued",
            "error": None,
        })
    return out
