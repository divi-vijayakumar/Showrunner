"""Sample mode — single 5-second teaser of the channel anchor.

One Seedream portrait + one Seedance clip of the anchor delivering the
open line + the picked story's headline. Skips research, debate, script
compile, and the multi-scene Seedance run. Cost ≈ $1 instead of $5–10
for a full tabloid. Used to verify face consistency, voice quality, and
lighting before committing to a full episode.

Called by `full._generate` after the story brief is ready when
`mode == "sample"`. Doesn't run standalone today.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid

from ....config import settings
from ....sdk.providers.ffmpeg import download_file
from ....sdk.providers.seedance import generate_seedance_clip
from ....sdk.providers.seedream import ensure_persona_portraits
from ....shows.the_tabloid.anchors import (
    as_persona,
    for_channel as anchor_for_channel,
    has_anchor,
)

log = logging.getLogger(__name__)


async def _generate_sample(
    segment_id: str,
    channel: str,
    story: dict,
    db,
) -> None:
    """Cheap 5-second quality preview. One Flux portrait + one Seedance
    clip of the anchor delivering the open line + the headline as a
    teaser."""
    if not has_anchor(channel):
        raise RuntimeError(f"Sample mode requires a defined anchor for channel {channel!r}")

    anchor = anchor_for_channel(channel)
    anchor_persona = as_persona(anchor)

    await db.update_segment(
        segment_id,
        {"status": "generating", "personas": [anchor_persona], "progress": 30},
    )

    # 1) Anchor portrait — skipped on text-to-video (no first frame anyway).
    is_t2v = settings().text_to_video
    if is_t2v:
        log.info("sample t2v: skipping portrait")
        anchor_portrait = None
    else:
        portraits = await ensure_persona_portraits([anchor_persona])
        anchor_portrait = portraits.get(anchor_persona["id"])
    await db.update_segment(segment_id, {"progress": 55})

    # 2) Single Seedance clip — anchor delivering the open line + the headline
    # as a broadcast teaser. On Seedance 2.0 with generate_audio=true the
    # spoken line in the prompt drives both the audio AND lip movement.
    teaser_line = f"{anchor.open_line} {story.get('headline','')}"
    accent = (anchor.voice or {}).get("accent_hint", "")
    accent_clause = f" Accent: {accent}." if accent else ""
    teaser_prompt = (
        f"SAME PERSON IN EVERY SCENE — {anchor.name}: {anchor.visual_description}\n\n"
        f"Camera: medium close-up, eye-line locked to camera, slow dolly-in "
        f"over 5 seconds. 9:16 vertical, 720p, cinematic broadcast feel. "
        f"Anchor speaks directly to camera with natural broadcast cadence."
        f"{accent_clause}\n\n"
        f"SPOKEN LINE (the anchor MUST speak this exact line, lip-synced, "
        f"clearly audible, no other dialogue, no music):\n"
        f'{anchor.name}: "{teaser_line}"'
    )

    seed = int(
        hashlib.sha1(f"sample:{anchor.id}:{segment_id[:6]}".encode()).hexdigest(), 16
    ) % 2**31
    clip_url = await generate_seedance_clip(
        prompt=teaser_prompt,
        camera_motion="dolly_in",
        duration=5,
        aspect_ratio="9:16",
        first_frame_image=anchor_portrait,
        seed=seed,
        generate_audio=True,
    )
    await db.update_segment(segment_id, {"progress": 90})

    # 3) Download the produced clip into our local persistent dir + register
    # it as the segment's media. No stitching, no overlays — it's a sample.
    work_dir = f"/tmp/tabloid_sample_{uuid.uuid4().hex[:8]}"
    os.makedirs(work_dir, exist_ok=True)
    final_path = os.path.join(work_dir, "sample.mp4")
    await download_file(clip_url, final_path)

    media_url = await db.upload_video(final_path, segment_id)
    await db.update_segment(
        segment_id,
        {
            "status": "ready",
            "progress": 100,
            "video_url": media_url,
            "media_url": media_url,
            "media_kind": "video",
        },
    )
    log.info("sample segment %s ready at %s", segment_id, media_url)
