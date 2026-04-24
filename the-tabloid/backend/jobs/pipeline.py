"""Celery task: full segment generation job.

Orchestrates the pipeline end-to-end:
  RSS → story select → debate (streams to Firestore) → script → Seedance clips →
  Seed Speech VO → infographics → ffmpeg stitch → upload → mark ready.

Each phase updates `progress` on the segment doc so the frontend can animate a bar.
"""
from __future__ import annotations

import asyncio
import logging

from celery import Celery

from ..agents.debate_engine import run_debate
from ..agents.script_compiler import compile_script
from ..agents.story_selector import enrich_with_bodies, fetch_headlines, select_story
from ..config import channel_or_raise, settings
from ..db.firestore import FirestoreClient
from ..personas import default_panel
from ..video.infographics import render_infographics
from ..video.seed_speech import generate_seed_speech
from ..video.seedance import generate_seedance_clip
from ..video.stitch import stitch_segment

log = logging.getLogger(__name__)


celery_app = Celery(
    "tabloid",
    broker=settings().redis_url,
    backend=settings().redis_url,
)
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=60 * 30,
    worker_prefetch_multiplier=1,
)


async def _generate(segment_id: str, channel: str, personas_override: list[dict] | None = None) -> None:
    db = FirestoreClient()
    cfg = channel_or_raise(channel)

    try:
        await db.update_segment(segment_id, {"status": "debate", "progress": 5})

        # 1. Fetch RSS + enrich top candidates with article bodies + select story
        headlines = await fetch_headlines(channel)
        enriched = await enrich_with_bodies(headlines)
        await db.update_segment(segment_id, {"progress": 10})
        story = await select_story(channel, enriched)
        await db.update_segment(
            segment_id,
            {
                "headline": story.get("headline"),
                "source": story.get("source"),
                "progress": 15,
            },
        )

        # 2. Personas
        personas = personas_override or default_panel(channel)
        await db.update_segment(segment_id, {"personas": personas})

        # 3. Debate (streams to Firestore as it goes)
        debate = await run_debate(segment_id, channel, story, personas, db)
        await db.update_segment(segment_id, {"status": "generating", "progress": 35})

        # 4. Broadcast script
        script = await compile_script(channel, story, debate)
        await db.update_segment(
            segment_id,
            {"progress": 40, "infographics": script.get("infographics", [])},
        )

        # 5. Seedance clips — sequential for QPS safety
        clip_urls: list[str] = []
        scenes = script["scenes"]
        for i, scene in enumerate(scenes):
            clip_url = await generate_seedance_clip(
                prompt=scene["seedance_prompt"],
                camera_motion=scene.get("camera_motion", "dolly_in"),
                duration=int(scene.get("duration", 5)),
                aspect_ratio="9:16",
            )
            clip_urls.append(clip_url)
            pct = 40 + int((i + 1) / len(scenes) * 35)
            await db.update_segment(segment_id, {"progress": pct})

        # 6. Seed Speech VO per line
        vo_clips: list[dict] = []
        for vo in script.get("vo_script", []):
            persona = next(
                (p for p in personas if p["name"] == vo.get("persona_name")),
                personas[0],
            )
            audio_url = await generate_seed_speech(
                text=vo.get("line", ""),
                voice_params=persona.get("voice", {}),
            )
            vo_clips.append(
                {"scene_index": int(vo.get("scene_index", 0)), "audio_url": audio_url}
            )
        await db.update_segment(segment_id, {"progress": 85})

        # 7. Render infographic overlays
        overlays = await render_infographics(script.get("infographics", []), story)

        # 8. Stitch
        final_path = await stitch_segment(
            clips=clip_urls,
            vo_clips=vo_clips,
            overlays=overlays,
            ticker_text=f"{story.get('headline', '')} · {story.get('source', '')} · THE TABLOID",
            channel_label=cfg["label"],
        )
        await db.update_segment(segment_id, {"progress": 95})

        # 9. Upload
        video_url = await db.upload_video(final_path, segment_id)

        # 10. Done
        await db.update_segment(
            segment_id,
            {"status": "ready", "progress": 100, "video_url": video_url},
        )
        log.info("segment %s ready at %s", segment_id, video_url)

    except Exception as exc:
        log.exception("Pipeline failure for segment %s", segment_id)
        await db.update_segment(
            segment_id, {"status": "failed", "error": str(exc)[:500]}
        )
        raise


@celery_app.task(name="tabloid.generate_segment")
def generate_segment(segment_id: str, channel: str, personas_override: list[dict] | None = None) -> None:
    """Celery entry point — runs the async pipeline to completion."""
    asyncio.run(_generate(segment_id, channel, personas_override))
