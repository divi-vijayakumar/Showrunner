"""Celery task: full segment generation job.

Orchestrates the pipeline end-to-end:
  RSS → story select → debate (streams to Firestore) → script → Seedance clips →
  Seed Speech VO → infographics → ffmpeg stitch → upload → mark ready.

Each phase updates `progress` on the segment doc so the frontend can animate a bar.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from celery import Celery

from ..agents.casting import cast_guests, panel_for
from ..agents.debate_engine import run_debate
from ..agents.research import research_story
from ..agents.script_compiler import compile_script
from ..agents.story_selector import (
    enrich_with_bodies,
    fetch_headlines,
    select_specific_story,
    select_story,
)
from ..anchors import as_persona, for_channel as anchor_for_channel, has_anchor
from ..config import channel_or_raise, settings
from ..db.firestore import FirestoreClient
from ..personas import default_panel  # legacy fallback if no anchor + casting fails
from ..video.infographics import render_infographics
from ..video.seed_speech import generate_seed_speech
from ..video.seedance import generate_seedance_clip
from ..video.seedream import ensure_persona_portraits
from ..video.stitch import download_file, stitch_podcast, stitch_segment

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


async def _generate(
    segment_id: str,
    channel: str,
    personas_override: list[dict] | None = None,
    mode: str = "tabloid",
    picked_story: dict | None = None,
) -> None:
    db = FirestoreClient()
    cfg = channel_or_raise(channel)

    try:
        await db.update_segment(segment_id, {"status": "debate", "progress": 5})

        if picked_story:
            # User chose this specific story in the UI — go straight to brief.
            await db.update_segment(segment_id, {"progress": 10})
            story = await select_specific_story(channel, picked_story)
            # Keep the candidate pool for the research agent's sibling matching.
            enriched = [picked_story]
        else:
            # Auto-pick path: fetch RSS, enrich, let the LLM choose.
            headlines = await fetch_headlines(channel)
            enriched = await enrich_with_bodies(headlines)
            await db.update_segment(segment_id, {"progress": 10})
            story = await select_story(channel, enriched)

        await db.update_segment(
            segment_id,
            {
                "headline": story.get("headline"),
                "source": story.get("source"),
                "story_brief": story,           # persist for the AgentLog UI
                "progress": 15,
            },
        )

        # 1b. Sample mode — cheap quality preview. Skip research, debate,
        # script compile, and the multi-scene Seedance run. Just one 5s
        # clip of the anchor delivering a teaser. ~$1 instead of ~$10.
        if mode == "sample":
            await _generate_sample(segment_id, channel, story, db)
            return

        # 2. Panel = channel anchor + 3 freshly-cast story-relevant guests.
        # personas_override (set by the user via PersonaSelect) wins over
        # everything; otherwise we cast on demand. If casting fails for any
        # reason, fall back to the legacy static personas.py panel so the
        # pipeline never strands at this step.
        if personas_override:
            log.info(
                "PANEL: using user-supplied personas_override (%d entries) — casting agent skipped. "
                "Names: %s",
                len(personas_override),
                [p.get("name") for p in personas_override],
            )
            personas = personas_override
        elif has_anchor(channel):
            anchor_persona = as_persona(anchor_for_channel(channel))
            log.info(
                "PANEL: calling casting agent for channel=%s anchor=%s",
                channel, anchor_persona.get("name"),
            )
            guests = await cast_guests(
                story=story, anchor=anchor_persona, channel_id=channel
            )
            log.info(
                "PANEL: casting returned %d guests: %s",
                len(guests), [g.get("name") for g in guests],
            )
            if len(guests) >= 3:
                personas = panel_for(anchor_persona=anchor_persona, guests=guests)
                log.info(
                    "PANEL: using anchor + cast guests: %s",
                    [p.get("name") for p in personas],
                )
            else:
                log.warning(
                    "PANEL: casting agent returned %d guests (need 3) — "
                    "falling back to default_panel",
                    len(guests),
                )
                personas = default_panel(channel)
        else:
            log.warning("PANEL: no anchor for channel=%s — using default_panel", channel)
            personas = default_panel(channel)

        await db.update_segment(segment_id, {"personas": personas})

        # 2b. Research agent → briefing for the debate
        briefing = await research_story(channel, story, related_pool=enriched)
        await db.update_segment(
            segment_id,
            {"briefing": briefing, "progress": 25},
        )

        # 3. Debate (streams to Firestore as it goes). Podcast mode runs
        # a longer turn order so the audio-only format has room to breathe.
        debate = await run_debate(
            segment_id, channel, story, personas, db,
            briefing=briefing,
            turn_count=16 if mode == "podcast" else 8,
        )
        await db.update_segment(segment_id, {"status": "generating", "progress": 35})

        # 3b. Podcast branch — skip video entirely. TTS every debate line
        # and concat into one .mp3. Fastest, cheapest form of the product.
        if mode == "podcast":
            await _generate_podcast(segment_id, db, debate, personas, story, cfg)
            return

        # 4. Broadcast script (tabloid/video path only)
        script = await compile_script(channel, story, debate, personas=personas)
        # Persist the full script so the AgentLog UI can show what each scene
        # was supposed to say + how it was framed. Strip the long
        # seedance_prompt fields — they're noise for the reader and bloat
        # the doc. Keep the human-readable parts.
        script_for_ui = {
            "scenes": [
                {
                    "scene_number": s.get("scene_number"),
                    "title": s.get("title"),
                    "featured_role": s.get("featured_role"),
                    "featured_persona_id": s.get("featured_persona_id"),
                    "duration": s.get("duration"),
                    "camera_motion": s.get("camera_motion"),
                    "shot": s.get("shot"),
                    "emotional_beat": s.get("emotional_beat"),
                    "vo_line": s.get("vo_line"),
                }
                for s in script.get("scenes", [])
            ],
            "infographics": script.get("infographics", []),
            "vo_script": script.get("vo_script", []),
        }
        await db.update_segment(
            segment_id,
            {
                "progress": 40,
                "infographics": script.get("infographics", []),
                "script": script_for_ui,
            },
        )

        # 4b. Persona reference portraits (one per persona, cached across
        # segments). Skipped entirely on text-to-video — no first frame in
        # play, character identity travels through the prompt instead.
        is_t2v = "text-to-video" in settings().fal_video_model
        if is_t2v:
            log.info("text-to-video mode — skipping portrait generation")
            portraits_by_persona = {}
        else:
            portraits_by_persona = await ensure_persona_portraits(personas)
        await db.update_segment(segment_id, {"progress": 45})

        # 5. Seedance clips — sequential for QPS safety; each solo scene uses
        # its featured persona's portrait as the first frame (img2video) so
        # the same "Kavitha" actually looks like the same Kavitha every time.
        # Cross-cut scenes (no single persona) fall back to the anchor's
        # portrait — img2video models need SOMETHING.
        anchor_id = next((p["id"] for p in personas if p.get("role") == "anchor"), None)
        anchor_frame = portraits_by_persona.get(anchor_id) if anchor_id else None
        personas_by_id = {p["id"]: p for p in personas}

        def _visual_lock(p: dict[str, Any]) -> str:
            """One fixed sentence repeated verbatim in every scene prompt so
            the video model can't re-imagine the persona's look per scene.

            In img2video, this short cue is enough — the first-frame image
            does the heavy lifting. In text-to-video there's no image to
            anchor to, so we splice the persona's full visual_description
            in verbatim every time."""
            voice = p.get("voice") or {}
            g = voice.get("gender", "")
            if is_t2v:
                viz = p.get("visual_description") or (
                    f"a {g} {p['role']} on a news debate show. "
                    f"Cultural context: {p.get('culture','')}. "
                    f"Personality: {p.get('style','')}"
                )
                # Wardrobe is the most reliable cross-scene identifier in t2v
                # — facial features drift, but a "deep burgundy structured
                # blazer + gold ear-cuff + bold lip" combo tends to render
                # consistently and is what viewers recognize. We hammer it.
                return (
                    f"SAME CHARACTER IN EVERY SCENE — {p['name']}: {viz} "
                    f"WARDROBE LOCK: keep {p['name']}'s exact outfit, accessories, "
                    f"hairstyle, and lighting setup identical to how they appear "
                    f"in any earlier scene of this segment. Same person, same age, "
                    f"same skin tone, same makeup, same posture archetype. "
                    f"If unsure, default to the description above verbatim."
                )
            return (
                f"SAME PERSON IN EVERY SCENE — {p['name']}: a {g} {p['role']}, "
                f"cultural context {p.get('culture','')}. "
                f"Match the provided first-frame image exactly — same face, "
                f"same hair, same wardrobe, same complexion, same age."
            )

        def _seed_for(persona_id: str | None) -> int:
            basis = (persona_id or "ensemble") + segment_id[:6]
            return int(hashlib.sha1(basis.encode()).hexdigest(), 16) % 2**31

        # Map scene_index → vo_line so we can fold dialogue INTO the Seedance
        # prompt. When using a model that generates native audio (Seedance on
        # Fal with generate_audio=true), this gives us lip-synced dialogue
        # with zero separate-TTS work.
        vo_by_scene: dict[int, dict[str, Any]] = {}
        for vo in script.get("vo_script", []):
            vo_by_scene[int(vo.get("scene_index", -1))] = vo

        native_audio = settings().video_provider == "fal" and "seedance" in settings().fal_video_model

        clip_urls: list[str] = []
        scenes = script["scenes"]
        for i, scene in enumerate(scenes):
            persona_id = scene.get("featured_persona_id")
            first_frame = portraits_by_persona.get(persona_id) if persona_id else None
            if not first_frame:
                first_frame = anchor_frame

            # Prepend the visual lock for the featured persona so the prompt
            # can't drift them across scenes. Gemini's generated prompt becomes
            # action/camera/emotion — the persona description is not optional.
            featured = personas_by_id.get(persona_id) if persona_id else None
            base_prompt = scene["seedance_prompt"]
            locked_prompt = (
                (_visual_lock(featured) + "\n\n" + base_prompt) if featured else base_prompt
            )

            # If the video model generates its own audio, splice the spoken
            # line straight into the prompt so Seedance knows what to say +
            # drives the lips to match.
            vo = vo_by_scene.get(i)
            if native_audio and vo and vo.get("line"):
                speaker = vo.get("persona_name") or (featured or {}).get("name") or "Anchor"
                locked_prompt = (
                    locked_prompt
                    + "\n\nSPOKEN LINE (lip-sync this exactly, natural delivery):\n"
                    + f"{speaker}: \"{vo['line'].strip()}\""
                )

            try:
                clip_url = await generate_seedance_clip(
                    prompt=locked_prompt,
                    camera_motion=scene.get("camera_motion", "dolly_in"),
                    duration=int(scene.get("duration", 5)),
                    aspect_ratio="9:16",
                    first_frame_image=first_frame,
                    seed=_seed_for(persona_id or anchor_id),
                )
            except Exception as exc:
                # Single-scene fault isolation. Most common cause: Fal's content
                # moderator flags one synthesized-audio clip as sensitive (e.g.
                # mentions of weapons / military / political figures), 422'ing
                # only that scene. Don't lose the 6 other paid clips — fall
                # back to a silent placeholder so stitch still produces a
                # cohesive episode.
                log.warning(
                    "Scene %d (%s) failed: %s — using silent placeholder",
                    i, scene.get("title", "?"), str(exc)[:300],
                )
                from ..video.seedance import _ensure_mock_clip
                clip_url = _ensure_mock_clip()
            clip_urls.append(clip_url)
            pct = 45 + int((i + 1) / len(scenes) * 30)
            await db.update_segment(segment_id, {"progress": pct})

        # 6. Voiceover. When the video model generates its own audio
        # (Seedance with generate_audio=true), skip separate TTS entirely —
        # the clips already contain lip-synced dialogue. Otherwise, TTS every
        # VO line via the configured TTS_PROVIDER and composite in stitch.
        vo_clips: list[dict] = []
        if native_audio:
            log.info("native_audio path: Seedance clips carry dialogue, skipping TTS")
        else:
            for vo in script.get("vo_script", []):
                persona = next(
                    (p for p in personas if p["name"] == vo.get("persona_name")),
                    personas[0],
                )
                voice_params = {
                    **(persona.get("voice") or {}),
                    "role": persona.get("role", ""),
                    "culture": persona.get("culture", ""),
                    "name": persona.get("name", ""),
                }
                audio_url = await generate_seed_speech(
                    text=vo.get("line", ""),
                    voice_params=voice_params,
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
            {
                "status": "ready",
                "progress": 100,
                "video_url": video_url,
                "media_url": video_url,
                "media_kind": "video",
            },
        )
        log.info("segment %s ready at %s", segment_id, video_url)

    except Exception as exc:
        log.exception("Pipeline failure for segment %s", segment_id)
        await db.update_segment(
            segment_id, {"status": "failed", "error": str(exc)[:500]}
        )
        raise


async def _generate_sample(
    segment_id: str,
    channel: str,
    story: dict,
    db,
) -> None:
    """Cheap 5-second quality preview. One Flux portrait + one Seedance clip
    of the anchor delivering the open line + the headline as a teaser.

    Skips: research agent, debate, script compile, multi-scene Seedance.
    Cost ≈ $1 instead of $5–10 for a full tabloid. Used to verify face
    consistency, voice quality, and lighting before committing to a full
    episode.
    """
    if not has_anchor(channel):
        raise RuntimeError(f"Sample mode requires a defined anchor for channel {channel!r}")

    anchor = anchor_for_channel(channel)
    anchor_persona = as_persona(anchor)

    await db.update_segment(
        segment_id,
        {"status": "generating", "personas": [anchor_persona], "progress": 30},
    )

    # 1) Anchor portrait — skipped on text-to-video (no first frame anyway).
    is_t2v = "text-to-video" in settings().fal_video_model
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

    seed = int(hashlib.sha1(f"sample:{anchor.id}:{segment_id[:6]}".encode()).hexdigest(), 16) % 2**31
    clip_url = await generate_seedance_clip(
        prompt=teaser_prompt,
        camera_motion="dolly_in",
        duration=5,
        aspect_ratio="9:16",
        first_frame_image=anchor_portrait,
        seed=seed,
    )
    await db.update_segment(segment_id, {"progress": 90})

    # 3) Download the produced clip into our local persistent dir + register
    # it as the segment's media. No stitching, no overlays — it's a sample.
    import os as _os, uuid as _uuid
    work_dir = f"/tmp/tabloid_sample_{_uuid.uuid4().hex[:8]}"
    _os.makedirs(work_dir, exist_ok=True)
    final_path = _os.path.join(work_dir, "sample.mp4")
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


async def _silent_fallback_url(text: str) -> str:
    """Best-effort silent mp3 sized to the line length. Used when a TTS call
    fails outright so one bad line doesn't kill the episode."""
    import os, subprocess, uuid
    out_dir = "/tmp/tabloid_podcast_silent"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"silent_{uuid.uuid4().hex[:8]}.mp3")
    secs = max(1.5, min(6.0, len(text) / 15))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{secs:.2f}",
                "-codec:a", "libmp3lame", "-b:a", "128k",
                path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        open(path, "wb").close()
    return f"file://{path}"


async def _generate_podcast(
    segment_id: str,
    db,
    debate: list[dict],
    personas: list[dict],
    story: dict,
    cfg: dict,
) -> None:
    """TTS each debate line and concat into a single mp3. No video work.

    Produces a natural listening experience: the 4 personas take turns
    over one continuous audio track, with ~300ms beat gaps between lines.
    """
    audio_clips: list[str] = []
    total = len(debate)

    # Pace the TTS loop — Google AI Studio's free TTS tier is ~15 RPM.
    # A 16-turn podcast at ~3s/call can tip the rolling window on the
    # last few lines. 1.5s inter-call spacing keeps us comfortably under.
    tts_pace_s = 1.5 if settings().tts_provider == "google" else 0.0

    for i, m in enumerate(debate):
        persona = next(
            (p for p in personas if p["name"] == m.get("persona_name")),
            personas[0],
        )
        voice_params = {
            **(persona.get("voice") or {}),
            "role": persona.get("role", ""),
            "culture": persona.get("culture", ""),
            "name": persona.get("name", ""),
        }
        try:
            audio_url = await generate_seed_speech(
                text=m.get("content", ""),
                voice_params=voice_params,
            )
        except Exception as exc:
            # Single-line fault isolation. If the TTS provider hangs or
            # errors on one debate line, keep the episode going with a
            # silent beat instead of crashing all 16 turns.
            log.warning("TTS failure on turn %d (%s) — inserting silent beat", i, exc)
            audio_url = await _silent_fallback_url(m.get("content", ""))
        audio_clips.append(audio_url)
        pct = 40 + int((i + 1) / total * 50)  # 40 → 90
        await db.update_segment(segment_id, {"progress": pct})
        if tts_pace_s and i + 1 < total:
            await asyncio.sleep(tts_pace_s)

    final_path = await stitch_podcast(
        audio_clips,
        headline=story.get("headline", ""),
        channel_label=cfg["label"],
    )
    await db.update_segment(segment_id, {"progress": 95})

    media_url = await db.upload_audio(final_path, segment_id)
    await db.update_segment(
        segment_id,
        {
            "status": "ready",
            "progress": 100,
            "video_url": media_url,      # reused field so the frontend can stay simple
            "media_url": media_url,
            "media_kind": "audio",
        },
    )
    log.info("podcast segment %s ready at %s", segment_id, media_url)


@celery_app.task(name="tabloid.generate_segment")
def generate_segment(
    segment_id: str,
    channel: str,
    personas_override: list[dict] | None = None,
    mode: str = "tabloid",
    picked_story: dict | None = None,
) -> None:
    """Celery entry point — runs the async pipeline to completion."""
    asyncio.run(_generate(segment_id, channel, personas_override, mode, picked_story))
