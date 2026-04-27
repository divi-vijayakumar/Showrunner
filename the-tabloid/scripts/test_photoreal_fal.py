"""One-off test: can Fal accept a photoreal portrait + photoreal i2v clip?

Goes through the same Fal moderation path the production pipeline uses:
  1. Flux Schnell with enable_safety_checker=True (image moderation)
  2. Seedance 2.0 fast image-to-video (video moderation on the input image)

Costs roughly $0.30-0.50 (1 Flux image + 1 Seedance 5s i2v clip).

Run from repo root:
    cd the-tabloid
    backend/.venv/bin/python scripts/test_photoreal_fal.py

Outcomes:
  - Flux returns no images / blank → safety_checker silently filtered it
  - Fal Seedance HTTP 422 → input image rejected by Seedance moderator
  - Both succeed → photoreal pipeline is viable on Fal
"""
from __future__ import annotations

import asyncio
import os
import sys

# Force i2v model BEFORE settings() reads env. The user's .env has the
# t2v variant; we override here so we exercise the i2v moderation path.
os.environ["FAL_VIDEO_MODEL"] = "bytedance/seedance-2.0/fast/image-to-video"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings  # noqa: E402
from backend.video.fal import generate_fal_image, generate_fal_video  # noqa: E402


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


async def main() -> None:
    cfg = settings()
    print("=" * 60)
    print("Photoreal viability test — Fal route")
    print("=" * 60)
    print(f"Image model:  {cfg.fal_image_model}")
    print(f"Video model:  {cfg.fal_video_model}")
    print(f"Mock mode:    {cfg.mock}")
    if cfg.mock:
        print("\n>>> TABLOID_MOCK=1 — flip to live mode for this test.")
        sys.exit(1)
    if not cfg.fal_api_key:
        print("\n>>> FAL_KEY not set.")
        sys.exit(1)
    print()

    print("Step 1/2: Photoreal portrait via Flux Schnell...")
    try:
        portrait_url = await generate_fal_image(
            PHOTOREAL_PORTRAIT_PROMPT, size="portrait_16_9"
        )
    except Exception as exc:
        print(f"\n  >>> FLUX REJECTED OR FAILED: {exc!r}")
        sys.exit(2)
    print(f"  ✓ Portrait URL: {portrait_url}")
    print()

    print("Step 2/2: Seedance i2v with photoreal first_frame...")
    try:
        video_url = await generate_fal_video(
            SEEDANCE_I2V_PROMPT,
            duration_s=5,
            first_frame_image=portrait_url,
            aspect_ratio="9:16",
        )
    except Exception as exc:
        print(f"\n  >>> SEEDANCE REJECTED OR FAILED: {exc!r}")
        print("  (HTTP 422 here = Fal's image moderator on Seedance i2v "
              "blocked the photoreal portrait.)")
        sys.exit(3)
    print(f"  ✓ Video URL: {video_url}")
    print()

    print("=" * 60)
    print("RESULT: BOTH FAL MODELS ACCEPTED PHOTOREAL CONTENT")
    print("=" * 60)
    print(f"Portrait: {portrait_url}")
    print(f"Video:    {video_url}")
    print()
    print("Photoreal pipeline is viable on the Fal route.")


if __name__ == "__main__":
    asyncio.run(main())
