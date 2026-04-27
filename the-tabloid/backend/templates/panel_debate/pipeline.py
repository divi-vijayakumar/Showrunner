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
import os
import tempfile
from typing import Any

from celery import Celery

from .agents.casting import cast_guests, panel_for
from .agents.debate import run_debate
from .agents.research import research_story
from .agents.script import compile_script
from .agents.story_selector import (
    enrich_with_bodies,
    fetch_headlines,
    select_specific_story,
    select_story,
)
from ...shows.the_tabloid.anchors import (
    as_persona,
    for_channel as anchor_for_channel,
    has_anchor,
)
from ...config import channel_or_raise, settings
from ...db.firestore import FirestoreClient
from ...shows.the_tabloid.personas import default_panel  # legacy fallback if no anchor + casting fails
from ...sdk.providers.infographics import render_infographics
from ...sdk.providers.seed_speech import generate_seed_speech
from ...sdk.providers.seedance import extract_last_frame, generate_seedance_clip
from ...sdk.providers.seedream import (
    ensure_master_stage_frame,
    ensure_panelist_mcus,
    ensure_persona_portraits,
)
from ...sdk.providers.ffmpeg import download_file, stitch_podcast, stitch_segment

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

        # 4b. Stage assets — locked master wide + per-panelist MCUs from
        # Seedream, plus persona portraits for any non-stage uses. Skipped
        # entirely on text-to-video; identity travels through the prompt then.
        is_t2v = "text-to-video" in settings().fal_video_model
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

        # Deterministic seating order at the desk, left to right as the
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
        # prompt. When using a model that generates native audio (Seedance on
        # Fal with generate_audio=true), this gives us lip-synced dialogue
        # with zero separate-TTS work.
        vo_by_scene: dict[int, dict[str, Any]] = {}
        for vo in script.get("vo_script", []):
            vo_by_scene[int(vo.get("scene_index", -1))] = vo

        native_audio = settings().video_provider == "fal" and "seedance" in settings().fal_video_model

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
            # the Clash scene, occasional gun/explosion sounds — which
            # Fal's audio moderator flags as sensitive and 422s the clip.
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
                from ...sdk.providers.seedance import _ensure_mock_clip
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


SEGMENTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "segments")
)


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

# Chained-edit constants. Scene 1 uses t2v (no prior frame to chain from);
# scenes 2+ use i2v with first_frame = (Seedream edit of prior scene's last
# frame, faces stripped so it passes Fal's i2v moderator).
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
# manifest + player UI. Numbers from Fal's published rates April 2026:
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


def _extract_last_frame_to_png(mp4_path: str, png_path: str) -> str | None:
    """ffmpeg: grab the final frame as a PNG. Returns path or None on failure."""
    import subprocess
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
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
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


async def _prep_chain_anchor(segment_id: str, scene_number: int, local_mp4: str) -> str | None:
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


async def _save_clip_locally(
    segment_id: str, scene_number: int, remote_url: str
) -> tuple[str, str] | tuple[None, None]:
    """Download a Fal CDN mp4 to data/segments/{sid}/scene_NN.mp4 and return
    (public_url, local_path). Returns (None, None) on failure.
    """
    from ...sdk.providers.ffmpeg import download_file
    slug = _scene_slug(scene_number)
    local_name = f"scene_{slug}.mp4"
    local_path = os.path.join(_segment_dir(segment_id), local_name)
    try:
        await download_file(remote_url, local_path)
    except Exception as exc:
        log.warning(
            "scene %d download failed for %s: %s",
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
    """Build the full 24-entry clips array with every scene marked 'queued'.
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


async def _generate_direct(
    segment_id: str,
    channel: str,
    script_data: dict[str, Any],
    *,
    start_scene: int = 1,
    end_scene: int | None = None,
) -> None:
    """Direct mode: take a hand-authored script JSON (story + panel + 24
    scenes with verbatim VO), run Seedance per scene, stash per-scene URLs
    on the segment doc + persist locally for hackathon output. NO debate
    engine, NO script compiler, NO ffmpeg concat.

    Batched generation: pass start_scene + end_scene (1-indexed inclusive)
    to render a slice. The on-disk manifest at data/segments/{sid}/
    manifest.json is the source of truth — preserves state across uvicorn
    restarts and across batches. Successfully-rendered clips are downloaded
    from Fal CDN to data/segments/{sid}/scene_NN.mp4 and re-served locally
    so we don't lose them when Fal's CDN URLs expire.

    Failure mode: any individual Seedance call that fails is recorded as
    {status: 'failed', error: '...'} on that scene; other scenes proceed.
    """
    db = FirestoreClient()
    personas = list(script_data.get("panel") or [])
    personas_by_id = {p["id"]: p for p in personas}
    scenes = list(script_data.get("scenes") or [])
    story = script_data.get("story") or {}

    if not scenes:
        await db.update_segment(
            segment_id,
            {"status": "failed", "error": "direct script has no scenes"},
        )
        return
    if not personas:
        await db.update_segment(
            segment_id,
            {"status": "failed", "error": "direct script has no panel"},
        )
        return

    # Resume / continuation: load existing manifest if any so a second batch
    # appends to the first batch's clips_state instead of overwriting.
    manifest = load_segment_manifest(segment_id) or {}
    clips_state: list[dict[str, Any]] = (
        manifest.get("clips") or _initial_clips_state(scenes, personas_by_id)
    )

    end_scene = end_scene if end_scene is not None else len(scenes)
    start_scene = max(1, int(start_scene))
    end_scene = min(len(scenes), int(end_scene))
    target_indices = list(range(start_scene - 1, end_scene))

    # Resolve the episode avatar URL that scene 1 will use as first_frame.
    # Two sources, in priority order:
    #   1. script_data.avatar_path: a local image file path (relative to repo
    #      root). Uploaded to Fal storage on first run, URL cached in a
    #      sidecar so we don't re-upload every batch.
    #   2. fallback: ensure_master_stage_frame generates a stylized cartoon
    #      avatar via Seedream/Flux. Used when no local file is provided.
    avatar_url: str | None = None
    if script_data.get("skip_avatar"):
        pass
    elif script_data.get("avatar_path"):
        avatar_url = await _resolve_local_avatar(script_data["avatar_path"])
        if avatar_url:
            log.info("Using local avatar (uploaded to Fal): %s", avatar_url)
        else:
            log.warning(
                "Local avatar path %s could not be resolved — scene 1 falls back to t2v",
                script_data["avatar_path"],
            )
    else:
        try:
            avatar_url = await ensure_master_stage_frame(channel, personas)
            if avatar_url:
                log.info("Episode avatar ready (Seedream): %s", avatar_url)
            else:
                log.warning("Avatar gen returned None — scene 1 will fall back to t2v")
        except Exception as exc:
            log.warning("Avatar gen failed: %s — scene 1 will fall back to t2v", exc)

    base_meta = {
        "segment_id": segment_id,
        "channel": channel,
        "mode": "direct",
        "avatar_url": avatar_url,
        "story": {
            "headline": story.get("headline", ""),
            "source": story.get("source", ""),
            "url": story.get("url", ""),
        },
        "panel": personas,
    }

    # Mark this batch's scenes as 'pending' upfront so the player sees them.
    for i in target_indices:
        if clips_state[i]["status"] not in ("ready",):
            clips_state[i]["status"] = "pending"
            clips_state[i]["error"] = None

    _write_segment_manifest(
        segment_id, {**base_meta, "clips": clips_state, "batch": [start_scene, end_scene]}
    )
    await db.update_segment(
        segment_id,
        {
            "status": "generating",
            "progress": 5,
            **base_meta,
            "clips": clips_state,
            "batch": [start_scene, end_scene],
        },
    )

    # Reuse the same panel-lock wrapper logic the main pipeline uses, since
    # the model still needs the locked-cast/locked-set context for every clip.
    _SEAT_ORDER = ["provocateur", "analyst", "anchor", "humanist"]
    personas_by_role = {p.get("role"): p for p in personas}
    seated_panel = [
        personas_by_role[r] for r in _SEAT_ORDER if r in personas_by_role
    ]

    def _panel_lock(featured: dict[str, Any] | None) -> str:
        panel_lines: list[str] = []
        for idx, p in enumerate(seated_panel):
            position = ["far left", "center-left", "center-right", "far right"][idx]
            viz = (p.get("visual_description") or "").strip()
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
            "Curved anchor desk centered. Back wall: large glowing letters "
            "reading exactly 'THE TABLOID' (T-H-E space T-A-B-L-O-I-D). "
            "Out-of-focus broadcast monitors behind the desk. Strong key "
            "light from camera-left, soft fill from camera-right, dark "
            "studio background. NOT a library. NOT a study. NO bookshelves. "
            "NO wood paneling. Modern glass-and-metal news set. The set is "
            "IDENTICAL in every scene of this episode.\n\n"
            "LOCKED CAST — EXACTLY FOUR PANELISTS, NOT THREE, NOT FIVE. "
            "Same four named people in every scene, in their fixed seating "
            "order at the desk:\n"
            f"{panel_block}\n\n"
            "DO NOT add any additional panelists. DO NOT replace any "
            "panelist. DO NOT introduce guest commentators or background "
            "people. The four panelists named above are the ONLY people "
            "on this set, ever.\n\n"
            f"{speaker_clause}\n\n"
            "WARDROBE LOCK — every panelist wears the EXACT outfit, hair, "
            "jewelry, and makeup described above in every scene. Same "
            "person, same clothes, same lighting. The set, cast, and "
            "lighting are invariants.\n\n"
            "CONTINUITY LOCK (across every scene of this episode):\n"
            "- Every panelist has the IDENTICAL face, IDENTICAL age, "
            "IDENTICAL hair (same color, same length, same style) in every "
            "scene. NO aging between scenes.\n"
            "- NO panelist gains gray hair. NO panelist gains wrinkles. "
            "NO panelist develops a beard or new facial hair. NO panelist "
            "appears older or younger than their stated age.\n"
            "- The featured speaker has the same face every scene they "
            "appear. The three background panelists (not currently "
            "speaking) have the same face every time they're visible at "
            "the edge of frame — DO NOT redraw them as different people.\n\n"
            "STYLE: Broadcast-polished, composed. The Daily Show / The "
            "Tabloid grammar — confident, locked, no kinetic camera tricks.\n\n"
            "BRAND LOCK — backdrop text reads 'THE TABLOID' (capital "
            "letters, exactly that spelling)."
        )

    native_audio = (
        settings().video_provider == "fal"
        and "seedance" in settings().fal_video_model
    )

    for batch_pos, i in enumerate(target_indices):
        scene = scenes[i]
        persona_id = scene.get("featured_persona_id")
        featured = personas_by_id.get(persona_id) if persona_id else None
        speaker_name = (
            (featured or {}).get("name") or scene.get("featured_role", "?")
        )
        vo_line = (scene.get("vo_line") or "").strip()

        # Endpoint-locked alternating-anchor pattern (no r2v, no Seedream
        # face-strip):
        #   - Scene 1: image_url=avatar, no end_image_url → free landing
        #   - Scene 2 (even): image_url=scene 1's last frame, end_image_url=avatar
        #   - Scene 3 (odd):  image_url=scene 2's last frame (=avatar), no end
        #   - Scene 4 (even): image_url=scene 3's last frame, end_image_url=avatar
        #   - ... and so on. Every even scene re-anchors to avatar at the end,
        #     so every odd scene STARTS on the avatar (moderator-safe).
        #     Even scenes' photoreal STARTS still risk 422 — halt-on-failure
        #     stops the run if so.
        prior_chain_url: str | None = None
        if i > 0:
            prior = clips_state[i - 1]
            if prior.get("status") == "ready":
                prior_chain_url = prior.get("chain_anchor_url")

        chosen_end_image: str | None = None
        scene_number = scene.get("scene_number", i + 1)
        # Parity by array index (i+1) so alternating pattern survives
        # inserted sub-scenes like "9b" with non-int scene_numbers.
        is_even_position = (i + 1) % 2 == 0
        if i == 0 and avatar_url:
            chosen_model = _I2V_MODEL
            first_frame = avatar_url
            mode_tag = "i2v-avatar"
        elif prior_chain_url:
            chosen_model = _I2V_MODEL
            first_frame = prior_chain_url
            # Even-position scenes re-anchor end to the avatar (panel wide reset).
            if avatar_url and is_even_position:
                chosen_end_image = avatar_url
                mode_tag = "i2v-locked"
            else:
                mode_tag = "i2v-chained"
        else:
            chosen_model = _T2V_MODEL
            first_frame = None
            mode_tag = "t2v"

        # Prompt construction splits by whether the model has an image
        # anchor (any i2v mode) or not (t2v fallback):
        #
        #   With an image: identity + set + wardrobe + cast continuity all
        #   come from the image. Prompt should be motion + dialogue + audio
        #   + style-stability suffix (~110-150 words). Re-describing what's
        #   in the image makes the model second-guess what it can already
        #   see and pushes outputs toward whatever the description sounds
        #   like (e.g. specific photoreal anatomy descriptions push outputs
        #   toward photoreal even when the image is stylized).
        #
        #   Without an image (t2v fallback): the prompt MUST carry full
        #   panel + cast + wardrobe info because there's no other anchor.
        if first_frame:
            # Lean motion-only prompt (~150-200 words). Identity comes from
            # the input image; prompt only carries motion + dialogue + a
            # strong speaker locator so the model focuses the right person.
            seat_idx = next(
                (j for j, p in enumerate(seated_panel)
                 if p.get("role") == (featured or {}).get("role")),
                None,
            )
            seat_label = (
                ["far left", "center-left", "center-right", "far right"][seat_idx]
                if seat_idx is not None else "their assigned seat"
            )
            visual_id = _SPEAKER_VISUAL_ID.get(persona_id or "", "")
            visual_id_clause = (
                f" Visual identifier (find this person in the input image): "
                f"{visual_id}."
                if visual_id else ""
            )
            motion_desc = (scene.get("seedance_prompt") or "").strip()
            end_lock_clause = (
                " End the clip on the master wide panel composition "
                "(matching the locked end-frame reference)."
                if mode_tag == "i2v-locked" else ""
            )
            spoken_line_block = (
                f"\n\n{speaker_name} speaks this line, lip-synced exactly at a "
                f"natural broadcast pace:\n\n\"{vo_line}\""
                if (native_audio and vo_line) else ""
            )
            locked_prompt = (
                f"FEATURED SPEAKER THIS SCENE: {speaker_name}, sitting at the "
                f"{seat_label} of the curved anchor desk.{visual_id_clause}\n\n"
                f"CAMERA FOCUS: The camera focuses on {speaker_name} at the "
                f"{seat_label} only. {speaker_name} is the ONLY panelist who "
                f"speaks or moves significantly in this scene. The other "
                f"three panelists are visible in the surrounding frame as "
                f"silent over-the-shoulder context — they do NOT speak, do "
                f"NOT lip-sync, do NOT make large gestures.{spoken_line_block}\n\n"
                f"Motion: {motion_desc}\n\n"
                "The other three panelists remain seated with subtle natural "
                "breathing only — no large movements, no expression changes, "
                "same faces as the input image.\n\n"
                f"Camera: see motion above.{end_lock_clause}\n\n"
                "Audio: studio dialogue and clean room tone only. No music, "
                "no sound effects, no stings.\n\n"
                "Match the input image's visual style EXACTLY — do not make "
                "it more photoreal or more stylized than the input. Preserve "
                "all four panelists' faces from the input image. The set, "
                "lighting, wardrobe, and seating order are already locked by "
                "the input image; do not redesign anything.\n\n"
                "Stable picture, faces stable with no deformation, natural "
                "smooth lip sync, no flickering."
            )
        else:
            # T2V fallback: needs the verbose panel+cast lock because there's
            # no image to anchor identity. Rare path on direct mode — only
            # hits when avatar generation failed.
            locked_prompt = (
                _panel_lock(featured)
                + "\n\nTHIS SCENE: "
                + (scene.get("seedance_prompt") or "").strip()
                + "\n\nAUDIO: Studio dialogue only. The speaker's voice over "
                "clean room tone. NO music, NO sound effects, NO dramatic "
                "stings, NO whip-pan whooshes, NO gunshots, NO explosions, "
                "NO breaking glass, NO action-movie ambient. Treat this as "
                "a calm news-debate set."
            )
            if native_audio and vo_line:
                locked_prompt = (
                    locked_prompt
                    + "\n\nSPOKEN LINE — DELIVER VERBATIM. The featured speaker "
                    "speaks this line in full within the 5-second clip. "
                    "DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add "
                    "words. DO NOT summarize. Lip-sync the speaker's mouth to "
                    "these exact words at a natural broadcast pace.\n"
                    + f"{speaker_name}: \"{vo_line}\""
                )

        scene_number = scene.get("scene_number", i + 1)
        log.info(
            "Direct scene %d (%s) — mode=%s, first_frame=%s",
            scene_number, speaker_name, mode_tag,
            "set" if first_frame else "none",
        )
        local_mp4_path: str | None = None
        try:
            clip_url = await generate_seedance_clip(
                prompt=locked_prompt,
                camera_motion=scene.get("camera_motion", "static"),
                duration=int(scene.get("duration", 5)),
                aspect_ratio="9:16",
                first_frame_image=first_frame,
                reference_image=None,
                seed=int(
                    hashlib.sha1(
                        ((persona_id or "ensemble") + segment_id[:6]).encode()
                    ).hexdigest(),
                    16,
                ) % 2**31,
                model_override=chosen_model,
                end_image_url=chosen_end_image,
            )
            # Persist locally so we don't lose it when Fal CDN URLs expire,
            # and so it survives across uvicorn restarts (mock store wipes).
            local_url, local_mp4_path = await _save_clip_locally(
                segment_id, scene_number, clip_url
            )
            clips_state[i]["video_url"] = local_url or clip_url
            clips_state[i]["remote_url"] = clip_url
            clips_state[i]["status"] = "ready"
            clips_state[i]["error"] = None
            clips_state[i]["mode"] = mode_tag
            clips_state[i]["cost_usd"] = _COST_BY_MODE.get(mode_tag, _COST_I2V)
        except Exception as exc:
            log.warning(
                "Direct scene %d (%s, mode=%s) failed: %s",
                scene_number, speaker_name, mode_tag, str(exc)[:300],
            )
            clips_state[i]["status"] = "failed"
            clips_state[i]["error"] = str(exc)[:500]
            clips_state[i]["mode"] = mode_tag

        # Prep chain anchor for the NEXT scene: extract this scene's last
        # frame, run Seedream face-strip, store the resulting URL on this
        # scene's clip state. Skip for the very last scene of the script
        # (nothing to chain into) and for failed scenes.
        if (
            clips_state[i]["status"] == "ready"
            and local_mp4_path
            and i + 1 < len(scenes)
        ):
            try:
                anchor_url = await _prep_chain_anchor(
                    segment_id, scene_number, local_mp4_path
                )
                if anchor_url:
                    clips_state[i]["chain_anchor_url"] = anchor_url
                    log.info(
                        "Chain anchor ready for scene %d→%d",
                        scene_number, scene_number + 1,
                    )
                else:
                    log.warning(
                        "Chain anchor prep returned None for scene %d — "
                        "next scene falls back to t2v",
                        scene_number,
                    )
            except Exception as exc:
                log.warning(
                    "Chain anchor prep crashed for scene %d: %s",
                    scene_number, str(exc)[:200],
                )

        # Halt-on-failure: chained pipeline depends on prior success.
        # If this scene failed, every subsequent scene in the batch is
        # wasted spend (no chain anchor available). Mark remaining as
        # 'skipped', persist, and break the loop. User can re-fire this
        # batch range after fixing the cause.
        if clips_state[i]["status"] == "failed":
            log.warning(
                "Halting batch at scene %d (%s) — chain broken. "
                "Remaining %d scenes marked 'skipped'.",
                scene_number, mode_tag,
                len(target_indices) - batch_pos - 1,
            )
            for skip_idx in target_indices[batch_pos + 1:]:
                if clips_state[skip_idx]["status"] in ("queued", "pending"):
                    clips_state[skip_idx]["status"] = "skipped"
                    clips_state[skip_idx]["error"] = (
                        f"Skipped: prior scene {scene_number} failed "
                        f"({mode_tag}); chain broken — re-fire after fix."
                    )
            _write_segment_manifest(
                segment_id,
                {**base_meta, "clips": clips_state, "batch": [start_scene, end_scene]},
            )
            await db.update_segment(
                segment_id,
                {"progress": int((batch_pos + 1) / len(target_indices) * 90),
                 "clips": list(clips_state)},
            )
            break

        # Persist after every scene — manifest is the durable record, segment
        # doc is the live UI feed. Both stay in sync.
        _write_segment_manifest(
            segment_id,
            {**base_meta, "clips": clips_state, "batch": [start_scene, end_scene]},
        )
        pct = 5 + int((batch_pos + 1) / len(target_indices) * 90)
        await db.update_segment(
            segment_id,
            {"progress": pct, "clips": list(clips_state)},
        )

    succeeded = sum(1 for c in clips_state if c["status"] == "ready")
    failed = sum(1 for c in clips_state if c["status"] == "failed")
    queued = sum(1 for c in clips_state if c["status"] == "queued")
    skipped = sum(1 for c in clips_state if c["status"] == "skipped")
    total_cost = round(sum(c.get("cost_usd", 0) for c in clips_state), 2)
    final_status = "ready" if (queued + skipped) == 0 else ("partial" if failed == 0 else "failed_chain")
    stats = {
        "total_scenes": len(clips_state),
        "succeeded": succeeded,
        "failed": failed,
        "queued": queued,
        "skipped": skipped,
        "total_cost_usd": total_cost,
        "last_batch": [start_scene, end_scene],
    }
    _write_segment_manifest(
        segment_id,
        {**base_meta, "clips": clips_state, "batch": [start_scene, end_scene],
         "stats": stats},
    )
    await db.update_segment(
        segment_id,
        {
            "status": final_status,
            "progress": 100 if queued == 0 else int((succeeded + failed) / len(clips_state) * 100),
            "clips": clips_state,
            "stats": stats,
        },
    )


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
