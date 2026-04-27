"""One-off test: Seedream → Seedance i2v via Fal (the cleanest "is photoreal
viable?" question we can answer without the ARK balance settled).

Why this is different from test_photoreal_fal.py:
  - That one used Flux Schnell for the image. Got rejected at Seedance with
    `partner_validation_failed` — possibly because Flux is not a ByteDance
    model, so ByteDance's moderator was extra suspicious.
  - This one uses Seedream v4.5 — same vendor as Seedance. If "partner"
    means "ByteDance trusts its own model output," this should pass.

Cost: ~$0.20 (Seedream $0.04 + Seedance fast i2v ~$0.15).

Run from repo root:
    cd the-tabloid
    backend/.venv/bin/python scripts/test_photoreal_fal_seedream.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

# Override video model to i2v BEFORE settings() reads env. The .env has the
# t2v variant; we want the i2v moderation path here.
os.environ["FAL_VIDEO_MODEL"] = "bytedance/seedance-2.0/fast/image-to-video"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings  # noqa: E402
from backend.video.fal import generate_fal_video  # noqa: E402


SEEDREAM_MODEL_ID = "fal-ai/bytedance/seedream/v4.5/text-to-image"
_FAL_QUEUE_BASE = "https://queue.fal.run"


PHOTOREAL_PORTRAIT_PROMPT = """
Cinematic photorealistic portrait of Veera Naatchi, a fictional Indian-English
broadcast journalist in her late 30s — host of THE TABLOID, a satirical news
debate show. SHE IS NOT A REAL PERSON. AI-generated fictional character.

Sharp shoulder-length straight black hair with a confident side part. One
statement gold ear-cuff, otherwise minimal jewelry. Tailored deep-burgundy
structured blazer over a crisp white shell. Bold lip color. Composed,
slightly forward-leaning posture. Direct, intelligent gaze. No smile in
resting frame — strategic, not theatrical.

Modern news-studio set: out-of-focus screens behind her, subtle THE TABLOID
glow on the back wall. Strong key light from camera-left, soft fill, dark
studio background. Medium close-up framing, eye-line locked to camera,
shallow depth of field.

9:16 vertical. High-resolution professional studio photography, sharp focus,
natural skin texture. Photorealistic.
""".strip()


SEEDANCE_I2V_PROMPT = """
The host Veera Naatchi at the curved anchor desk of THE TABLOID. Slight
forward lean, eyes locked to the lens, composed posture. She gestures
decisively toward camera as she names the question viewers are actually
asking. Strategic intensity, no smile.

Camera motion: slow dolly in (push) toward the speaker. Start moving at
clip open, decelerate over the final 0.8 seconds, end on a stable held
frame.

Aspect ratio: 9:16. Broadcast-polished composition.
""".strip()


def _fal_headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {settings().fal_api_key}",
        "Content-Type": "application/json",
    }


async def gen_seedream_portrait() -> str:
    """Direct Fal Seedream v4.5 call — bypasses generate_fal_image which is
    Flux-shaped (num_inference_steps etc., not in Seedream's schema)."""
    payload: dict[str, Any] = {
        "prompt": PHOTOREAL_PORTRAIT_PROMPT,
        "image_size": "portrait_16_9",
        "num_images": 1,
        "enable_safety_checker": True,
    }
    submit_url = f"{_FAL_QUEUE_BASE}/{SEEDREAM_MODEL_ID}"
    print(f"  POST {submit_url}")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(submit_url, headers=_fal_headers(), json=payload)
        if resp.status_code >= 400:
            print(f"\n  >>> SEEDREAM SUBMIT REJECTED: HTTP {resp.status_code}")
            print(f"  >>> Response: {resp.text[:1500]}")
            sys.exit(2)
        submit_data = resp.json()
        request_id = submit_data.get("request_id")
        status_url = submit_data.get("status_url") or (
            f"{_FAL_QUEUE_BASE}/{SEEDREAM_MODEL_ID}/requests/{request_id}/status"
        )
        result_url = submit_data.get("response_url") or (
            f"{_FAL_QUEUE_BASE}/{SEEDREAM_MODEL_ID}/requests/{request_id}"
        )
        print(f"  request: {request_id}")

        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2.0)
            r = await client.get(status_url, headers=_fal_headers())
            if r.status_code >= 400:
                print(f"\n  >>> SEEDREAM POLL REJECTED: HTTP {r.status_code}")
                print(f"  >>> Response: {r.text[:1500]}")
                sys.exit(3)
            state = (r.json().get("status") or "").upper()
            print(f"  poll: status={state}")
            if state == "COMPLETED":
                break
            if state in ("FAILED", "ERROR", "CANCELLED"):
                print(f"\n  >>> SEEDREAM JOB {state}: {r.text[:1500]}")
                sys.exit(4)
        else:
            print("\n  >>> SEEDREAM TIMEOUT")
            sys.exit(5)

        r = await client.get(result_url, headers=_fal_headers())
        if r.status_code >= 400:
            print(f"\n  >>> SEEDREAM RESULT REJECTED: HTTP {r.status_code}")
            print(f"  >>> Response: {r.text[:1500]}")
            sys.exit(6)
        data = r.json()

    images = data.get("images") or []
    if not images:
        print(f"\n  >>> SEEDREAM no images in response: {data}")
        sys.exit(7)
    img_url = images[0].get("url") if isinstance(images[0], dict) else None
    if not img_url:
        print(f"\n  >>> SEEDREAM image shape unexpected: {images[0]}")
        sys.exit(8)
    return img_url


async def main() -> None:
    cfg = settings()
    print("=" * 60)
    print("Photoreal viability test — Seedream → Seedance via Fal")
    print("=" * 60)
    print(f"Seedream model: {SEEDREAM_MODEL_ID}")
    print(f"Seedance model: {cfg.fal_video_model}")
    print(f"Mock mode:      {cfg.mock}")
    if cfg.mock:
        print("\n>>> TABLOID_MOCK=1 — flip to live mode for this test.")
        sys.exit(1)
    if not cfg.fal_api_key:
        print("\n>>> FAL_KEY not set.")
        sys.exit(1)
    print()

    print("Step 1/2: Photoreal portrait via Seedream v4.5...")
    portrait_url = await gen_seedream_portrait()
    print(f"  ✓ Portrait URL: {portrait_url}")
    print()

    print("Step 2/2: Seedance i2v with the Seedream photoreal first_frame...")
    try:
        video_url = await generate_fal_video(
            SEEDANCE_I2V_PROMPT,
            duration_s=5,
            first_frame_image=portrait_url,
            aspect_ratio="9:16",
        )
    except Exception as exc:
        print(f"\n  >>> SEEDANCE REJECTED OR FAILED: {exc!r}")
        print("  (HTTP 422 here = ByteDance moderator blocks photoreal "
              "even when the source is its own Seedream model.)")
        sys.exit(9)
    print(f"  ✓ Video URL: {video_url}")
    print()

    print("=" * 60)
    print("RESULT: SEEDREAM → SEEDANCE PHOTOREAL ACCEPTED")
    print("=" * 60)
    print(f"Portrait: {portrait_url}")
    print(f"Video:    {video_url}")
    print()
    print("Photoreal pipeline is viable when the source image is Seedream.")


if __name__ == "__main__":
    asyncio.run(main())
