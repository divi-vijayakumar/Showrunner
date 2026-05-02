"""Full production mode — RSS → research → debate → script → video.

The "real" panel-debate pipeline. Walks every agent in order:

  story_selector  picks (or accepts) a headline + body
  casting         builds the panel for THIS story
  research        produces a grounded briefing
  debate          simulates the panel debate
  script          compiles the broadcast script (scenes + VO + infographics)
  set/storyboard  generates the master stage frame + per-panelist MCUs
                  (fold of casting+set asset agents)
  director        per-scene first-frame + camera-motion + locked prompt
                  (currently inline in this loop; will migrate)
  executor        Seedance i2v, last-frame chaining
  editor          ffmpeg stitch + ticker + lower thirds + upload

Per-scene fault isolation: a single Seedance failure inserts a silent
placeholder rather than killing the whole episode.

Sample mode (mode == "sample") forks out to `_generate_sample` after the
story brief is ready. Direct mode does NOT route through this — it has
its own short-circuited file (`direct.py`).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from typing import Any

from celery import Celery

from ..agents.casting import cast_guests, panel_for
from ..agents.debate import run_debate
from ..agents.research import research_story
from ..agents.script import compile_script
from ..agents.story_selector import (
    enrich_with_bodies,
    fetch_headlines,
    select_specific_story,
    select_story,
)
from ....config import channel_or_raise, settings
from ....db.firestore import FirestoreClient
from ....sdk.providers.ffmpeg import download_file, stitch_segment
from ....sdk.providers.infographics import render_infographics
from ....sdk.providers.seed_speech import generate_seed_speech
from ....sdk.providers.seedance import extract_last_frame, generate_seedance_clip
from ....sdk.providers.seedream import (
    ensure_master_stage_frame,
    ensure_panelist_mcus,
    ensure_persona_portraits,
)
from ....shows.the_tabloid.anchors import (
    as_persona,
    for_channel as anchor_for_channel,
    has_anchor,
)
from ....shows.the_tabloid.personas import default_panel  # legacy fallback if no anchor + casting fails
from .sample import _generate_sample

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

        # 3. Debate (streams to Firestore as it goes).
        debate = await run_debate(
            segment_id, channel, story, personas, db,
            briefing=briefing,
            turn_count=8,
        )
        await db.update_segment(segment_id, {"status": "generating", "progress": 35})

        # 4. Broadcast script
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

        # 4b. Stage assets — locked master wide + per-panelist MCUs from
        # Seedream, plus persona portraits for any non-stage uses. Skipped
        # entirely on text-to-video; identity travels through the prompt then.
        is_t2v = settings().text_to_video
        if is_t2v:
            log.info("text-to-video mode — skipping stage frame generation")
            portraits_by_persona = {}
            master_stage_url: str | None = None
            mcus_by_persona: dict[str, str] = {}
        else:
            (
                portraits_by_persona,
                master_stage_url,
                mcus_by_persona,
            ) = await asyncio.gather(
                ensure_persona_portraits(personas),
                ensure_master_stage_frame(channel, personas),
                ensure_panelist_mcus(channel, personas),
            )
        await db.update_segment(segment_id, {"progress": 45})

        # 5. Seedance clips — sequential because we chain each clip's last
        # frame into the next one's first_frame when the speaker matches.
        # Per-scene first_frame strategy:
        #   - same speaker as previous scene → chain from previous last frame
        #   - different speaker → that panelist's MCU derivative
        #   - no specific speaker (cross/clash) → master stage wide
        # Master wide is also passed as `reference_image` on EVERY scene so
        # cast/wardrobe/set lighting stay locked across the whole episode.
        anchor_id = next((p["id"] for p in personas if p.get("role") == "anchor"), None)
        personas_by_id = {p["id"]: p for p in personas}

        # Seat order at the desk, left-to-right from the viewer's POV.
        # Matches the avatar image (The_Tabloid_set.png):
        #   far left = provocateur (Amit), center-left = humanist (Anjali),
        #   center-right = anchor (Veera), far right = analyst (Arjun).
        _SEAT_ORDER = ["provocateur", "humanist", "anchor", "analyst"]
        personas_by_role = {p.get("role"): p for p in personas}
        seated_panel = [
            personas_by_role[r] for r in _SEAT_ORDER if r in personas_by_role
        ]

        def _t2v_panel_lock(featured: dict[str, Any] | None) -> str:
            """T2V wrapper. Re-enumerates the locked set + every panelist's
            visual_description in every prompt, with seating order, exact
            count, and explicit no-new-members rule.

            Failure modes this is fighting (observed in earlier runs):
              - panel count drifts to 3 or 5 — fixed with EXACTLY FOUR rule
              - cast identity drifts per clip — fixed with named seating order
              - set drifts to wood-paneled library — fixed with explicit
                "modern broadcast studio, NOT library, NOT bookshelves" rule
              - brand text misspelled (TABLOND/TABLOD) — fixed by repeating
                "THE TABLOID" exact spelling multiple times
            """
            panel_lines: list[str] = []
            for idx, p in enumerate(seated_panel):
                position = ["far left", "center-left", "center-right", "far right"][idx]
                voice = p.get("voice") or {}
                g = voice.get("gender", "")
                viz = (p.get("visual_description") or "").strip()
                if not viz:
                    viz = (
                        f"a {g} {p.get('role','')}, cultural context: "
                        f"{p.get('culture','')}, personality: {p.get('style','')}"
                    )
                panel_lines.append(
                    f"  · SEAT {idx + 1} ({position}): {p.get('name','?')} "
                    f"({p.get('role','?')}). {viz}"
                )
            panel_block = "\n".join(panel_lines)

            speaker = (featured or {}).get("name") if featured else None
            speaker_role = (featured or {}).get("role") if featured else None
            if speaker:
                seat_idx = next(
                    (i for i, p in enumerate(seated_panel)
                     if p.get("role") == speaker_role),
                    None,
                )
                seat_label = (
                    ["far left", "center-left", "center-right", "far right"][seat_idx]
                    if seat_idx is not None
                    else "their assigned seat"
                )
                speaker_clause = (
                    f"FEATURED SPEAKER THIS SCENE: {speaker} (sitting at "
                    f"{seat_label} of the desk). Camera focuses on {speaker}; "
                    f"the other three panelists remain seated and visible "
                    f"at the desk in the surrounding frame (over-shoulder)."
                )
            else:
                speaker_clause = (
                    "ENSEMBLE SCENE: all four panelists visible at the desk, "
                    "seated in their fixed positions."
                )

            return (
                "LOCKED SET — modern broadcast news-debate studio of THE TABLOID. "
                "A curved anchor desk dominates the center of the frame. The "
                "back wall has the show's branded backlight: large glowing "
                "letters reading exactly 'THE TABLOID' (T-H-E space T-A-B-L-O-I-D). "
                "Out-of-focus broadcast monitors are visible behind the desk. "
                "Strong key light from camera-left, soft fill from camera-right, "
                "dark studio background. NOT a library. NOT a study. NO "
                "bookshelves. NO wood paneling. NO globes. This is a glass-and-"
                "metal modern news set, not a bookish interior. The set is "
                "IDENTICAL in every scene of this episode.\n\n"
                "LOCKED CAST — EXACTLY FOUR PANELISTS, NOT THREE, NOT FIVE. "
                "Same four named people in every scene, in their fixed seating "
                "order at the desk (left-to-right as the viewer sees them):\n"
                f"{panel_block}\n\n"
                "DO NOT add any additional panelists. DO NOT replace any "
                "panelist with a different person. DO NOT remove any panelist. "
                "DO NOT introduce guest commentators, hosts, or background "
                "people. The four panelists named above are the ONLY people "
                "on this set, ever.\n\n"
                f"{speaker_clause}\n\n"
                "WARDROBE LOCK — every panelist wears the EXACT outfit, hair, "
                "jewelry, and makeup described above in every scene of this "
                "episode. Same person, same clothes, same lighting. The set "
                "is fixed. The four panelists are fixed. Only the camera "
                "framing changes between scenes.\n\n"
                "BRAND LOCK — the backdrop text reads 'THE TABLOID' (capital "
                "letters, exactly that spelling). Render only this brand text; "
                "do not invent other branding."
            )

        def _stage_lock_wrapper(
            scene: dict[str, Any], featured: dict[str, Any] | None
        ) -> str:
            """The i2v stage-lock prefix. Rides in front of the director's
            seedance_prompt so the model knows the set/cast/wardrobe/lighting
            are constants and only camera framing changes between clips.

            The reference_image (master wide) carries the actual visual lock;
            this wrapper just tells the model to honor it strictly."""
            speaker = (featured or {}).get("name") or "the panel"
            return (
                "SET: The locked broadcast stage of THE TABLOID — curved "
                "anchor desk, four seated panelists, THE TABLOID backlight, "
                "out-of-focus broadcast screens, dark studio background, key "
                "light from camera-left. This stage is shown in the reference "
                "image and is constant across every scene of this episode. "
                "DO NOT redesign the desk, panelists, wardrobe, hair, makeup, "
                "jewelry, lighting, or backdrop. DO NOT add or remove "
                "panelists. The set, cast, and lighting are invariants.\n\n"
                f"FRAMING: Focus on {speaker}. The other panelists remain "
                "seated and visible at the desk in over-the-shoulder context "
                "— do not remove them. Maintain every character's face, "
                "wardrobe, and seat position exactly consistent with the "
                "reference image.\n\n"
                "STYLE: Broadcast-polished, composed. The Daily Show / The "
                "Tabloid grammar — confident, locked, no kinetic camera tricks."
            )

        def _seed_for(persona_id: str | None) -> int:
            basis = (persona_id or "ensemble") + segment_id[:6]
            return int(hashlib.sha1(basis.encode()).hexdigest(), 16) % 2**31

        def _pick_first_frame(
            scene: dict[str, Any],
            prev_scene: dict[str, Any] | None,
            prev_last_frame_url: str | None,
        ) -> str | None:
            """Selective chaining. Same-speaker adjacency → chain from the
            previous clip's last frame. Speaker change or multi-speaker →
            use the appropriate Seedream-cached frame."""
            persona_id = scene.get("featured_persona_id")
            prev_persona_id = (prev_scene or {}).get("featured_persona_id")
            if (
                persona_id
                and prev_persona_id
                and persona_id == prev_persona_id
                and prev_last_frame_url
            ):
                return prev_last_frame_url
            if not persona_id:
                return master_stage_url
            return (
                mcus_by_persona.get(persona_id)
                or master_stage_url
                or portraits_by_persona.get(persona_id)
            )

        # Map scene_index → vo_line so we can fold dialogue INTO the Seedance
        # prompt. With generate_audio=true, Seedance lip-syncs the SPOKEN LINE
        # block directly, so we don't need a separate TTS pass.
        vo_by_scene: dict[int, dict[str, Any]] = {}
        for vo in script.get("vo_script", []):
            vo_by_scene[int(vo.get("scene_index", -1))] = vo

        # Seedance 2.0 produces synchronized lip-synced audio when
        # generate_audio=true. When native_audio is True we skip the
        # separate Seed Speech TTS step and let the video model speak the
        # SPOKEN LINE block directly. Mock provider is silent.
        native_audio = settings().video_provider == "byteplus"

        clip_urls: list[str] = []
        scenes = script["scenes"]
        prev_scene: dict[str, Any] | None = None
        prev_last_frame_url: str | None = None
        for i, scene in enumerate(scenes):
            persona_id = scene.get("featured_persona_id")
            featured = personas_by_id.get(persona_id) if persona_id else None

            if is_t2v:
                first_frame = None
                base_prompt = scene["seedance_prompt"]
                locked_prompt = (
                    _t2v_panel_lock(featured)
                    + "\n\nTHIS SCENE: "
                    + base_prompt
                )
            else:
                first_frame = _pick_first_frame(scene, prev_scene, prev_last_frame_url)
                locked_prompt = (
                    _stage_lock_wrapper(scene, featured)
                    + "\n\nTHIS SCENE: "
                    + scene["seedance_prompt"]
                )

            # Audio direction. Seedance keeps inferring dramatic SFX —
            # whoosh stings on whip-pan scenes, action-movie ambient on
            # the Clash scene, occasional gun/explosion sounds — which the
            # ARK audio moderator flags as sensitive and fails the clip.
            # Force studio-dialogue-only for every scene.
            locked_prompt = (
                locked_prompt
                + "\n\nAUDIO: Studio dialogue only. The speaker's voice "
                "over clean room tone. NO music, NO sound effects, NO "
                "dramatic stings, NO whip-pan whooshes, NO gunshots, "
                "NO explosions, NO breaking glass, NO action-movie "
                "ambient. Treat this as a calm news-debate set."
            )

            # If the video model generates its own audio, splice the spoken
            # line straight into the prompt. Seedance MUST deliver this exact
            # text — no trimming, no paraphrasing, no summarizing. The line
            # is already word-budgeted to fit a 5-second clip at broadcast pace.
            vo = vo_by_scene.get(i)
            if native_audio and vo and vo.get("line"):
                speaker = vo.get("persona_name") or (featured or {}).get("name") or "Anchor"
                locked_prompt = (
                    locked_prompt
                    + "\n\nSPOKEN LINE — DELIVER VERBATIM. The featured speaker "
                    "speaks this line in full within the 5-second clip. "
                    "DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add "
                    "words. DO NOT summarize. Lip-sync the speaker's mouth to "
                    "these exact words at a natural broadcast pace.\n"
                    + f"{speaker}: \"{vo['line'].strip()}\""
                )

            try:
                clip_url = await generate_seedance_clip(
                    prompt=locked_prompt,
                    camera_motion=scene.get("camera_motion", "dolly_in"),
                    duration=int(scene.get("duration", 5)),
                    aspect_ratio="9:16",
                    first_frame_image=first_frame,
                    reference_image=master_stage_url if not is_t2v else None,
                    seed=_seed_for(persona_id or anchor_id),
                    generate_audio=native_audio,
                )
            except Exception as exc:
                # Single-scene fault isolation. Most common cause: the ARK
                # content moderator flags one synthesized-audio clip as
                # sensitive (e.g. mentions of weapons / military / political
                # figures), failing only that scene. Don't lose the 6 other
                # paid clips — fall back to a silent placeholder so stitch
                # still produces a cohesive episode.
                log.warning(
                    "Scene %d (%s) failed: %s — using silent placeholder",
                    i, scene.get("title", "?"), str(exc)[:300],
                )
                from ....sdk.providers.seedance import _ensure_mock_clip
                clip_url = _ensure_mock_clip()
            clip_urls.append(clip_url)

            # Chain prep: extract the last frame of this clip for the next
            # scene. Best-effort — if download/extract/upload fails, the next
            # scene falls back to its MCU derivative via _pick_first_frame.
            chained_url: str | None = None
            if not is_t2v and clip_url and not clip_url.startswith("file://"):
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        local_mp4 = os.path.join(tmp, f"scene_{i}.mp4")
                        await download_file(clip_url, local_mp4)
                        local_png = os.path.join(tmp, f"scene_{i}_last.png")
                        if extract_last_frame(local_mp4, local_png):
                            chained_url = await db.upload_image(
                                local_png, segment_id, f"scene{i}_last"
                            )
                except Exception as exc:
                    log.warning(
                        "Last-frame chain prep failed for scene %d: %s — "
                        "next scene will use MCU derivative",
                        i, str(exc)[:200],
                    )
            prev_scene = scene
            prev_last_frame_url = chained_url

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
