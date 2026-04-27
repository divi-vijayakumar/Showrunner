"""Minimal-viable test of the endpoint-locked i2v architecture.

Stage 1: Generate kf_03 (scene 2's END frame) via Seedream v4.5 edit.
  - @Image1 = The_Tabloid_set.png (cast anchor, identity + style + seat order)
  - @Image2 = scene 1's last frame (set + lighting continuity)
  - Composition: MCU on Veera, end-of-scene-2 pose

Stage 2: Animate scene 2 via Seedance i2v with locked endpoints.
  - image_url = scene 1's last frame (same as kf_02)
  - end_image_url = kf_03 (just generated)
  - Motion-only prompt, no panel bible

Cost: ~$0.05 (Seedream edit) + ~$2.42 (Seedance 10s i2v) ≈ $2.47.

Run:
    cd the-tabloid
    backend/.venv/bin/python scripts/test_endpoint_locked_scene2.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import dotenv
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SEGMENT_ID = "seg_291a1d4f6b"

CAST_ANCHOR_LOCAL = os.path.join(REPO_ROOT, "scripts", "The_Tabloid_set.png")
CAST_ANCHOR_URL_CACHE = CAST_ANCHOR_LOCAL + ".fal_url.txt"

# scene 1's last frame already extracted + uploaded to Fal earlier
KF_02_URL = (
    "https://v3b.fal.media/files/b/0a97e2d1/"
    "m2CaFChQrVQ3dnzmvuLIu_chain_anchor_after_scene_01.png"
)

SEEDREAM_EDIT_MODEL = "fal-ai/bytedance/seedream/v4.5/edit"
SEEDANCE_I2V_MODEL = "bytedance/seedance-2.0/fast/image-to-video"

# --- Stage 1 prompt: generate kf_03 (scene 2's end frame) ---
KF_03_PROMPT = """Generate a single still image: a frame from THE TABLOID, an Indian \
news debate panel show, in the same render style as the reference images.

Reference @Image1 (cast anchor sheet) for the four panelists' identities, seat \
order, wardrobe, and the show's exact render style. Match their faces, hair, \
ages, and outfits exactly to @Image1. Do not invent new panelists. Exactly \
four panelists, in this seat order from left to right: Amit Sharma \
(provocateur, far left, navy bandhgala), Arjun Rao (analyst, center-left, \
charcoal blazer + wire-frame glasses), Veera Naatchi (anchor, center-right, \
deep-burgundy structured blazer + gold ear-cuff), Anjali Mehta (humanist, \
far right, forest-green silk blouse).

Reference @Image2 (the immediately prior frame) for set continuity, lighting \
state, camera framing baseline, and overall rendering style. Match the studio \
set, the THE TABLOID backdrop letters, the curved anchor desk, the monitor \
wall, and the lighting. The render style of THIS frame must match @Image2 — \
do NOT make it more cartoon or more photoreal than @Image2.

Composition for THIS frame: medium close-up on Veera Naatchi (anchor, \
center-right of the desk). She has just finished addressing the panel. Her \
right hand is resting back on the desk after a sweeping gesture across the \
panel. Head facing slightly toward Amit at her left, mouth closed, neutral \
composed expression. The other three panelists visible at the edges of frame, \
seated, composed, looking toward Veera.

9:16 vertical, sharp focus, broadcast composition.""".strip()


# --- Stage 2 prompt: animate scene 2 (motion + dialogue + audio + stability) ---
SCENE_2_PROMPT = """The featured speaker (Veera Naatchi, anchor at center-right of \
the curved anchor desk) speaks this line, lip-synced exactly at a natural \
broadcast pace:

"Skyroot just flagged Vikram One off, to Sri Hari Kota. Telangana's Chief \
Minister attended. Launch window, is June. Three views, one rocket."

Motion: smooth, subtle. Veera's head turns slightly toward Amit at her left \
on the opening line, right hand sweeps gently across the panel on "three \
views, one rocket", returns to rest on the desk by the end. The other three \
panelists remain seated with subtle natural breathing — no large movements, \
no expression changes.

Camera: locked, no movement.

Audio: studio dialogue and clean room tone only. No music, no sound effects.

Stable picture, face stable no deformation, natural smooth lip sync, \
no flickering.""".strip()


async def upload_cast_anchor() -> str:
    """Upload The_Tabloid_set.png to Fal storage if not already cached."""
    if os.path.exists(CAST_ANCHOR_URL_CACHE):
        cached = open(CAST_ANCHOR_URL_CACHE).read().strip()
        if cached.startswith("http"):
            return cached
    if not os.path.exists(CAST_ANCHOR_LOCAL):
        raise RuntimeError(f"cast anchor not found: {CAST_ANCHOR_LOCAL}")
    os.environ.setdefault("FAL_KEY", settings().fal_api_key)
    import fal_client
    url = await asyncio.to_thread(fal_client.upload_file, CAST_ANCHOR_LOCAL)
    with open(CAST_ANCHOR_URL_CACHE, "w") as f:
        f.write(url)
    return url


async def gen_kf_03(cast_anchor_url: str, kf_02_url: str) -> str:
    """Stage 1: Seedream v4.5 edit with two reference images."""
    os.environ.setdefault("FAL_KEY", settings().fal_api_key)
    import fal_client
    result = await asyncio.to_thread(
        fal_client.subscribe,
        SEEDREAM_EDIT_MODEL,
        arguments={
            "image_urls": [cast_anchor_url, kf_02_url],
            "prompt": KF_03_PROMPT,
            "num_images": 1,
            "enable_safety_checker": True,
        },
    )
    images = result.get("images") or []
    if not images:
        raise RuntimeError(f"Seedream returned no images: {result}")
    url = images[0].get("url") if isinstance(images[0], dict) else None
    if not url:
        raise RuntimeError(f"Seedream image shape unexpected: {images[0]}")
    return url


async def animate_scene_2(kf_02_url: str, kf_03_url: str) -> str:
    """Stage 2: Seedance i2v with both image_url and end_image_url (endpoint-locked)."""
    os.environ.setdefault("FAL_KEY", settings().fal_api_key)
    import fal_client
    result = await asyncio.to_thread(
        fal_client.subscribe,
        SEEDANCE_I2V_MODEL,
        arguments={
            "image_url": kf_02_url,
            "end_image_url": kf_03_url,
            "prompt": SCENE_2_PROMPT,
            "duration": "10",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "generate_audio": True,
        },
    )
    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        url = result.get("video_url")
    if not url:
        raise RuntimeError(f"Seedance returned no video: {result}")
    return url


async def main() -> None:
    print("=" * 70)
    print("ENDPOINT-LOCKED i2v TEST — scene 2 between two locked keyframes")
    print("=" * 70)
    print()

    print("Step 1/3: Resolve cast anchor URL (The_Tabloid_set.png)...")
    cast_url = await upload_cast_anchor()
    print(f"  ✓ cast_anchor: {cast_url}")
    print()

    print(f"Step 2/3: Seedream v4.5 edit → kf_03 (scene 2 end frame)...")
    print(f"  @Image1 = cast_anchor")
    print(f"  @Image2 = kf_02 (scene 1 last frame)")
    try:
        kf_03_url = await gen_kf_03(cast_url, KF_02_URL)
    except Exception as exc:
        print(f"  >>> Seedream edit FAILED: {exc!r}")
        sys.exit(1)
    print(f"  ✓ kf_03: {kf_03_url}")
    print()

    print(f"Step 3/3: Seedance i2v → scene 2 (kf_02 → kf_03, 10s)...")
    print(f"  image_url      = kf_02")
    print(f"  end_image_url  = kf_03")
    try:
        video_url = await animate_scene_2(KF_02_URL, kf_03_url)
    except Exception as exc:
        print(f"  >>> Seedance i2v FAILED: {exc!r}")
        sys.exit(2)
    print(f"  ✓ scene 2 video: {video_url}")
    print()

    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print(f"kf_02 (scene 1's last frame, START):    {KF_02_URL}")
    print(f"kf_03 (Seedream-generated, END):        {kf_03_url}")
    print(f"scene 2 mp4 (interpolated 10s clip):    {video_url}")
    print()
    print("Open all three to inspect:")
    print(f"  - Does kf_03 look photoreal + match cast/style/setting?")
    print(f"  - Does scene 2 video stay photoreal start to end?")
    print(f"  - Does the cast lock survive (all 4 panelists consistent)?")
    print(f"  - Does it land cleanly on kf_03 by the end?")


if __name__ == "__main__":
    asyncio.run(main())
