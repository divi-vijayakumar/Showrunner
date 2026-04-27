"""One-off test: does Fal Seedance i2v accept a still frame extracted from
Seedance t2v output as its first_frame? If yes → chaining is the real fix
for cast/camera continuity across scenes.

Hypothesis: t2v output passes Fal's content moderator (we just generated
5 t2v clips successfully). Extracting a still frame from that output and
feeding it to i2v should also pass — because the visual content is
identical to what t2v just rendered.

If this passes, we wire chaining into _generate_direct so each scene N+1
opens on the exact ending frame of scene N — guarantees cast identity AND
camera continuity in one move.

Cost: ~$1.20 (one Seedance i2v fast clip).

Run:
    cd the-tabloid
    backend/.venv/bin/python scripts/test_chain_t2v_to_i2v.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile

os.environ["FAL_VIDEO_MODEL"] = "bytedance/seedance-2.0/fast/image-to-video"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings  # noqa: E402
from backend.db.firestore import FirestoreClient  # noqa: E402
from backend.video.fal import generate_fal_video  # noqa: E402

SOURCE_SEG = "seg_7491e21ca5"
SOURCE_SCENE = 1  # which scene's last frame to chain from
SOURCE_MP4 = (
    "/Users/dhivyavijayakumar/git/SWSCD/the-tabloid/data/segments/"
    f"{SOURCE_SEG}/scene_{SOURCE_SCENE:02d}.mp4"
)


# Same prompt scene 2 used in the t2v batch — minus the SPOKEN LINE clause
# (we want pure i2v continuity test; can re-add audio after we know it works).
SCENE_2_PROMPT = """LOCKED SET — modern broadcast news-debate studio of THE TABLOID. \
Curved anchor desk centered. Back wall: large glowing letters reading exactly \
'THE TABLOID' (T-H-E space T-A-B-L-O-I-D). Out-of-focus broadcast monitors \
behind the desk. Strong key light from camera-left, soft fill from camera-right, \
dark studio background. NOT a library. NO bookshelves. Modern glass-and-metal \
news set. The set is IDENTICAL in every scene of this episode.

LOCKED CAST — EXACTLY FOUR PANELISTS, NOT THREE, NOT FIVE. Same four named \
people in every scene, in their fixed seating order at the desk:
  · SEAT 1 (far left): Murugan Selvam (provocateur). South Asian man, mid-50s, \
weathered features, salt-and-pepper full hair brushed back. Dark navy bandhgala-\
style buttoned jacket over a cream collarless shirt.
  · SEAT 2 (center-left): Arjun Subramaniam (analyst). South Asian man, mid-40s, \
slim build, thin wire-frame glasses. Charcoal-grey single-breasted blazer over a \
pale blue oxford shirt, no tie. Salt-and-pepper close-cropped hair.
  · SEAT 3 (center-right): Veera Naatchi (anchor). Modern Indian woman, late 30s. \
Sharp shoulder-length straight black hair. Burgundy structured blazer. One gold \
ear-cuff. Bold lip color.
  · SEAT 4 (far right): Anjali Mehta (humanist). South Asian woman, late 30s. \
Shoulder-length wavy black hair. Forest-green silk blouse with small gold pendant.

DO NOT add or replace any panelist. The four panelists are the ONLY people on this set.

THIS SCENE: Veera medium close-up, slight forward lean, hand resting on the desk, \
naming the story with measured authority. Held frame from start to end. Do not \
re-light or re-stage.

AUDIO: Studio dialogue only. Clean room tone. NO music. NO sound effects."""


def extract_last_frame(mp4_path: str, out_png: str) -> str:
    """ffmpeg: grab the final frame as a PNG."""
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-sseof", "-0.1",
            "-i", mp4_path,
            "-vframes", "1",
            "-q:v", "2",
            "-update", "1",
            out_png,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not os.path.exists(out_png) or os.path.getsize(out_png) == 0:
        raise RuntimeError("ffmpeg produced empty PNG")
    return out_png


async def main() -> None:
    print("=" * 70)
    print("Chain test: Seedance t2v output → Seedance i2v first_frame")
    print("=" * 70)
    print(f"Source mp4: {SOURCE_MP4}")
    if not os.path.exists(SOURCE_MP4):
        print(">>> source mp4 not found")
        sys.exit(1)
    print(f"Image provider: {settings().image_provider}")
    print(f"Video model:    {settings().fal_video_model}")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        png_local = os.path.join(tmp, f"chain_{SOURCE_SEG}_scene_{SOURCE_SCENE}.png")
        print("Step 1/3: Extract last frame from scene 1 mp4...")
        extract_last_frame(SOURCE_MP4, png_local)
        print(f"  ✓ PNG: {png_local} ({os.path.getsize(png_local)} bytes)")
        print()

        print("Step 2/3: Upload PNG to Fal storage (returns fal.media URL)...")
        # Fal's own storage — no Firebase / no public host setup needed.
        # FAL_KEY env must be set; fal_client picks it up automatically.
        os.environ.setdefault("FAL_KEY", settings().fal_api_key)
        import fal_client  # noqa: E402
        png_url = await asyncio.to_thread(fal_client.upload_file, png_local)
        print(f"  ✓ Fal URL: {png_url}")
        print()

        print("Step 3/3: Seedance i2v with the chained PNG as first_frame...")
        try:
            video_url = await generate_fal_video(
                SCENE_2_PROMPT,
                duration_s=5,
                first_frame_image=png_url,
                aspect_ratio="9:16",
            )
        except Exception as exc:
            print(f"\n  >>> SEEDANCE i2v REJECTED OR FAILED: {exc!r}")
            print("  If HTTP 422 with content_policy_violation, the moderator "
                  "rejects t2v-derived frames too — chaining isn't viable on "
                  "Fal. Fall back to text-only continuity prompts.")
            sys.exit(3)
        print(f"  ✓ Video URL: {video_url}")
        print()

    print("=" * 70)
    print("RESULT: CHAINING WORKS — t2v output frame accepted by i2v moderator")
    print("=" * 70)
    print(f"Chained scene 2 mp4: {video_url}")
    print()
    print("Next step: wire chaining into _generate_direct so every scene N+1 "
          "opens on scene N's last frame. Cast identity + camera continuity "
          "guaranteed.")


if __name__ == "__main__":
    asyncio.run(main())
