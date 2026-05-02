"""Short Drama pipeline — runs the 5 agents end-to-end and writes status to
the same Firestore segment doc the panel_debate flow uses, so the existing
Player.tsx polling works unchanged.

Status progression (frontend reads this to update its progress bar):
  status=stylizing       progress 5..25  — Pixarify characters + sets in parallel
  status=writing         progress 25..40 — writer agent produces scene plan
  status=set_design      progress 40..50 — generate any sets the writer flagged
  status=generating      progress 50..85 — Seedance scene generation (parallel by key)
  status=stitching       progress 85..95 — editor stitch
  status=ready           progress 100    — final mp4 served

On failure the segment doc gets status=failed + error string, mirroring
panel_debate's error path.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from ...config import settings
from ...db.firestore import FirestoreClient
from .agents.casting import cast_for_brief, generate_cast_portraits
from .agents.director import render_scene
from .agents.editor import stitch_film
from .agents.keyframes import generate_scene_keyframes
from .agents.set_designer import resolve_all_sets
from .agents.stylize import pixarify_characters
from .agents.writer import scene_plan_from_brief

log = logging.getLogger(__name__)


# Module-level set of segment_ids the user has asked us to abandon. The
# pipeline checks this between stages (and inside the per-scene loop) and
# raises CancelledError so the run exits cleanly without killing the whole
# uvicorn process. Already-issued Seedream/Seedance HTTP calls aren't
# revoked — those tasks were paid for the moment they were created — but
# we stop polling/issuing new ones.
_CANCELLED_SEGMENTS: set[str] = set()


def cancel_segment(segment_id: str) -> bool:
    """Mark a segment as cancelled so its pipeline aborts at the next check.
    Returns True if the segment is now marked, False if it was already so."""
    if segment_id in _CANCELLED_SEGMENTS:
        return False
    _CANCELLED_SEGMENTS.add(segment_id)
    log.warning("[drama %s] cancellation requested by user", segment_id)
    return True


def is_cancelled(segment_id: str) -> bool:
    return segment_id in _CANCELLED_SEGMENTS


class _DramaCancelled(Exception):
    """Raised inside the pipeline when the segment is cancelled."""


async def _silent_placeholder_clip() -> str:
    """If a single Seedance scene fails, slot in a 5s black clip so the
    rest of the film still stitches. Cached on disk."""
    from ...sdk.providers.seedance import _ensure_mock_clip
    return _ensure_mock_clip()


async def generate_drama(
    segment_id: str,
    *,
    beat_sheet: str,
    characters: list[dict[str, Any]],
    sets: list[dict[str, Any]],
    default_language: str = "tamil",
    title: str = "Short Drama",
) -> None:
    """Runs the full short-drama pipeline. `characters`/`sets` are lists of
    {id, label, local_path, description}. Status updates flow to Firestore."""
    db = FirestoreClient()

    # The six production stages we surface to the demo audience. Each entry
    # tracks status + start/finish + a short detail line. DramaProgress.tsx
    # renders this as a checklist next to the live log scroller.
    import time as _time

    # Real-film order: writer invents the story first, then we cast actors
    # to the script's roles, then direct, then shoot, then edit. Uploaded
    # character photos still flow in as Script input hints.
    STAGE_ORDER = [
        "Script",
        "Casting",
        "Direction",
        "Production",
        "Editing",
    ]
    pipeline_steps: list[dict[str, Any]] = [
        {"name": n, "status": "pending", "detail": ""} for n in STAGE_ORDER
    ]
    log_lines: list[dict[str, Any]] = []
    current_stage = {"name": None}

    async def _push() -> None:
        await db.update_segment(
            segment_id,
            {"pipeline_steps": list(pipeline_steps), "log_lines": list(log_lines)},
        )

    def _abort_if_cancelled() -> None:
        """Raise to bail out of the pipeline if the user clicked Cancel.
        Caller's `except _DramaCancelled` block handles cleanup + status."""
        if segment_id in _CANCELLED_SEGMENTS:
            raise _DramaCancelled(f"segment {segment_id} cancelled by user")

    async def _stage_start(name: str, detail: str = "") -> None:
        current_stage["name"] = name
        for s in pipeline_steps:
            if s["name"] == name:
                s["status"] = "in_progress"
                s["started_at"] = _time.time()
                s["detail"] = detail
                break
        await _log(f"▶ {name} started{(' · ' + detail) if detail else ''}")
        await _push()

    async def _stage_done(name: str, detail: str = "") -> None:
        for s in pipeline_steps:
            if s["name"] == name:
                s["status"] = "done"
                s["completed_at"] = _time.time()
                if detail:
                    s["detail"] = detail
                break
        await _log(f"✓ {name} complete{(' · ' + detail) if detail else ''}")
        await _push()

    async def _stage_skip(name: str, reason: str) -> None:
        for s in pipeline_steps:
            if s["name"] == name:
                s["status"] = "skipped"
                s["detail"] = reason
                break
        await _log(f"↷ {name} skipped · {reason}")
        await _push()

    async def _stage_fail(name: str, reason: str) -> None:
        for s in pipeline_steps:
            if s["name"] == name:
                s["status"] = "failed"
                s["detail"] = reason
                break
        await _log(f"✗ {name} failed · {reason}", level="error")
        await _push()

    async def _log(message: str, level: str = "info") -> None:
        entry = {"ts": _time.time(), "level": level, "message": message,
                 "stage": current_stage["name"]}
        log_lines.append(entry)
        if level == "warn":
            log.warning("[drama %s] %s", segment_id, message)
        elif level == "error":
            log.error("[drama %s] %s", segment_id, message)
        else:
            log.info("[drama %s] %s", segment_id, message)
        await db.update_segment(segment_id, {"log_lines": list(log_lines)})

    try:
        await db.update_segment(
            segment_id,
            {
                "status": "writing",
                "progress": 5,
                "template": "short_drama",
                "headline": title,
                "drama_brief": beat_sheet[:1200],
                "default_language": default_language,
                "pipeline_steps": list(pipeline_steps),
                "log_lines": [],
            },
        )

        # Public preview URLs the frontend can render in the asset grid.
        public_base = settings().public_base_url.rstrip("/")

        def _preview_url(asset_id: str) -> str:
            for ext in ("jpg", "jpeg", "png", "webp"):
                if os.path.exists(os.path.join(
                    settings().tabloid_data_dir, "drama_assets", f"{asset_id}.{ext}"
                )):
                    return f"{public_base}/api/drama/assets/{asset_id}.{ext}"
            return ""

        character_labels = [c["label"] for c in characters]
        uploaded_set_labels = [s["label"] for s in sets]
        char_preview_urls_input = {
            c["label"]: _preview_url(c["id"]) for c in characters
        }
        set_preview_urls_input = {
            s["label"]: _preview_url(s["id"]) for s in sets
        }
        await db.update_segment(
            segment_id,
            {
                "character_urls_original": char_preview_urls_input,
                "set_urls_uploaded_original": set_preview_urls_input,
            },
        )

        # ------------------ STAGE 1: Script (Writer LLM) -------------------
        # The writer goes first. It reads the brief + any uploaded character
        # labels and produces a typed scene plan. The writer is allowed to
        # invent NEW character labels as needed — Casting will then fill in
        # descriptions + Pixar portraits for whatever the writer named.
        _abort_if_cancelled()
        await _stage_start(
            "Script",
            f"Writer (BytePlus Seed LLM) · brief {len(beat_sheet)} chars · "
            f"default {default_language} · {len(character_labels)} uploaded character hint(s)",
        )
        plan = await scene_plan_from_brief(
            beat_sheet=beat_sheet,
            character_labels=character_labels,
            set_labels=uploaded_set_labels,
            default_language=default_language,
            cast_descriptions={
                c["label"]: c.get("description", "") for c in characters
            },
        )
        scenes = plan.get("scenes", [])
        sets_to_generate = plan.get("sets_to_generate", [])
        outro_card = plan.get("outro_card") or None
        scene_plan_payload = {
            "title": plan.get("title", title),
            "scenes": scenes,
            "sets_to_generate": sets_to_generate,
            "outro_card": outro_card,
        }
        await db.update_segment(
            segment_id, {"progress": 40, "scene_plan": scene_plan_payload},
        )
        await _log(
            f"Writer returned {len(scenes)} scenes, {len(sets_to_generate)} sets to generate, "
            f"outro card={outro_card.get('text') if outro_card else 'none'}"
        )
        for sc in scenes:
            line = sc.get("spoken_line", "").strip()
            await _log(
                f"  Scene {sc.get('scene_number')} · {sc.get('duration_s')}s · {sc.get('language')} · "
                f"{sc.get('speaker_label')} → \"{line[:80]}{'…' if len(line) > 80 else ''}\""
            )
        await _stage_done("Script", f"{len(scenes)} scenes drafted")

        # ------------------ STAGE 2: Casting -------------------------------
        # Read the writer's scene plan, collect every character label the
        # writer named, and produce a Pixar portrait + description for each.
        # Uploaded photos run through Pixarify i2i in parallel; remaining
        # labels go through the cast-list LLM + Seedream t2i. Uploads win
        # on label conflicts.
        await db.update_segment(segment_id, {"status": "casting", "progress": 25})

        # Pull labels the writer used in any scene (speaker + visible).
        writer_labels: set[str] = set()
        for sc in scenes:
            sp = sc.get("speaker_label")
            if sp and sp != "narrator":
                writer_labels.add(sp)
            for vc in sc.get("visible_characters") or []:
                if vc:
                    writer_labels.add(vc)

        # Labels the user uploaded photos for vs labels the casting LLM has
        # to invent descriptions for.
        uploaded_set = set(character_labels)
        labels_to_invent = [l for l in writer_labels if l not in uploaded_set]

        _abort_if_cancelled()
        await _stage_start(
            "Casting",
            f"{len(uploaded_set)} uploaded character(s) → Pixarify i2i; "
            f"{len(labels_to_invent)} writer-named character(s) → describe + Seedream t2i",
        )
        if writer_labels:
            await _log(
                "Writer asked for cast: " + ", ".join(sorted(writer_labels))
            )

        # Pixarify uploaded photos (i2i — preserves face identity).
        pixarify_task = pixarify_characters(characters)

        # LLM proposes descriptions for the writer-invented labels. We give
        # it a synthetic "brief" naming each missing label so it returns a
        # cast entry per label.
        async def _invent_cast() -> list[dict[str, Any]]:
            if not labels_to_invent:
                return []
            invent_brief = (
                "STORY BRIEF:\n" + beat_sheet
                + "\n\nThe writer's scene plan named these character labels "
                "that need descriptions and Pixar portraits: "
                + ", ".join(labels_to_invent)
            )
            return await cast_for_brief(invent_brief, character_labels)

        invent_task = _invent_cast()
        char_urls, casted = await asyncio.gather(pixarify_task, invent_task)

        # Filter casted to only the labels the writer actually used.
        casted = [c for c in casted if c["label"] in writer_labels]
        await _log(
            f"Pixarified {len(char_urls)}/{len(uploaded_set)} uploaded character(s); "
            f"casting LLM produced {len(casted)} new entries"
        )

        # Generate Pixar portraits for the casted (non-uploaded) members.
        casted_portraits = await generate_cast_portraits(casted)
        await _log(
            f"Generated {len(casted_portraits)}/{len(casted)} casted Pixar portraits"
        )

        # Merge: uploaded portraits win over casted on label collisions.
        merged_char_urls: dict[str, str] = {**casted_portraits, **char_urls}
        cast_descriptions: dict[str, str] = {
            c["label"]: c.get("visual_description", "") for c in casted
        }
        for c in characters:
            cast_descriptions.setdefault(c["label"], c.get("description", ""))

        # Sets too — Pixarify uploaded set photos in parallel with the
        # casting LLM. Even if the writer doesn't reference an uploaded set,
        # we Pixarify it (small cost, useful for the UI preview).
        set_urls_uploaded = await resolve_all_sets(sets, [])
        set_preview_urls = {
            s["label"]: _preview_url(s["id"]) for s in sets
            if s["label"] in set_urls_uploaded
        }
        char_preview_urls = {
            c["label"]: _preview_url(c["id"]) for c in characters
            if c["label"] in char_urls
        }

        await db.update_segment(
            segment_id,
            {
                "progress": 35,
                "casted_members": casted,
                "casted_portraits": casted_portraits,
                "character_urls": merged_char_urls,
                "set_urls_uploaded": set_urls_uploaded,
                "character_urls_original": char_preview_urls,
                "set_urls_uploaded_original": set_preview_urls,
            },
        )
        await _stage_done(
            "Casting",
            f"{len(merged_char_urls)} cast member(s) ready · "
            f"{len(set_urls_uploaded)} uploaded set(s) Pixarified",
        )

        # ------------------ STAGE 3: Direction -----------------------------
        # Generate sets the writer flagged as `__generate__`, build the
        # per-scene casting assignment table, and compose the Seedance prompt
        # for every scene.
        await db.update_segment(segment_id, {"status": "directing", "progress": 40})
        _abort_if_cancelled()
        await _stage_start(
            "Direction",
            f"Generating {len(sets_to_generate)} set master(s); "
            f"composing per-scene Seedance prompts",
        )
        if sets_to_generate:
            await _log(
                "Generating set masters via ARK Seedream t2i: "
                + ", ".join(s["id"] for s in sets_to_generate)
            )
            generated_set_urls = await resolve_all_sets([], sets_to_generate)
            await _log(
                f"Generated {len(generated_set_urls)}/{len(sets_to_generate)} set master(s)"
            )
        else:
            generated_set_urls = {}
            await _log("No new sets to generate — uploaded sets cover the script")

        all_set_urls: dict[str, str] = {**set_urls_uploaded, **generated_set_urls}
        all_set_preview_urls: dict[str, str] = {
            **set_preview_urls,
            **generated_set_urls,
        }

        casting_table: list[dict[str, Any]] = []
        for sc in scenes:
            speaker_label = sc.get("speaker_label", "narrator")
            set_label = sc.get("set_label", "__generate__")
            speaker_url = (
                merged_char_urls.get(speaker_label)
                if speaker_label != "narrator" else None
            )
            visible_urls = {
                lbl: merged_char_urls[lbl]
                for lbl in sc.get("visible_characters", [])
                if lbl in merged_char_urls
            }
            casting_table.append({
                "scene_number": sc.get("scene_number"),
                "title": sc.get("title"),
                "speaker_label": speaker_label,
                "speaker_anchor_url": speaker_url,
                "set_label": set_label,
                "set_anchor_url": all_set_preview_urls.get(set_label),
                "visible_characters": list(visible_urls.keys()),
                "visible_character_urls": visible_urls,
            })
            await _log(
                f"  Scene {sc.get('scene_number')} cast · "
                f"speaker={speaker_label}{' (narrator)' if speaker_label == 'narrator' else ''} · "
                f"set={set_label}"
            )

        await db.update_segment(
            segment_id,
            {
                "progress": 50,
                "set_urls": all_set_preview_urls,
                "set_urls_generated": generated_set_urls,
                "casting_table": casting_table,
            },
        )

        from .agents.director import build_scene_prompt
        char_descriptions = dict(cast_descriptions)
        for c in characters:
            char_descriptions.setdefault(c["label"], c.get("description", ""))
        direction_table: list[dict[str, Any]] = []
        for sc in scenes:
            prompt_text = build_scene_prompt(sc, char_descriptions)
            direction_table.append({
                "scene_number": sc.get("scene_number"),
                "title": sc.get("title"),
                "duration_s": sc.get("duration_s"),
                "language": sc.get("language"),
                "camera_motion": sc.get("camera_motion"),
                "audio_direction": sc.get("audio_direction"),
                "spoken_line": sc.get("spoken_line"),
                "seedance_prompt": prompt_text,
            })
            await _log(
                f"  Scene {sc.get('scene_number')} direction · {sc.get('camera_motion')} · "
                f"prompt {len(prompt_text)} chars"
            )
        await db.update_segment(
            segment_id, {"progress": 53, "direction_table": direction_table},
        )

        # Generate per-scene keyframes (showrunner endpoint-locked path).
        # Sequential — each keyframe references the previous for continuity.
        await _log(
            f"Generating {len(scenes)} per-scene keyframe(s) via Seedream i2i "
            "(seeded by dominant character portrait → chained)…"
        )
        keyframe_urls = await generate_scene_keyframes(
            scenes,
            cast_descriptions=char_descriptions,
            cast_portraits=merged_char_urls,
        )
        ok_count = sum(1 for k in keyframe_urls if k)
        await _log(
            f"Generated {ok_count}/{len(scenes)} keyframe(s) "
            f"({len(scenes) - ok_count} fell back to multi-reference)"
        )
        await db.update_segment(
            segment_id, {"progress": 58, "keyframe_urls": keyframe_urls},
        )
        await _stage_done(
            "Direction",
            f"{len(direction_table)} prompts + {ok_count}/{len(scenes)} keyframes ready",
        )

        # ------------------ STAGE 4: Production ----------------------------
        await db.update_segment(segment_id, {"status": "generating", "progress": 60})
        _abort_if_cancelled()
        await _stage_start(
            "Production",
            f"{len(scenes)} Seedance 2.0 clip(s) in parallel · 4-key BytePlus pool · generate_audio=true",
        )

        async def _one_scene(idx: int, scene: dict[str, Any]) -> str:
            # Don't kick off a NEW Seedance task if the user cancelled while
            # earlier scenes were in flight. Already-issued tasks complete
            # naturally (we paid for them); this one shorts to a placeholder.
            if segment_id in _CANCELLED_SEGMENTS:
                placeholder = await _silent_placeholder_clip()
                await db.update_segment(
                    segment_id,
                    {
                        f"scene_status.{idx}": {
                            "scene_number": scene.get("scene_number"),
                            "status": "cancelled",
                            "clip_url": placeholder,
                        }
                    },
                )
                return placeholder
            set_label = scene.get("set_label", "__generate__")
            set_url = all_set_urls.get(set_label)
            # Endpoint-locked i2v: first_frame=kf_n, last_frame=kf_n+1.
            # The keyframes were generated upstream via Seedream i2i with
            # cast portraits as seeds — that's where the cast continuity
            # is locked. Director only animates between the two.
            kf_url = keyframe_urls[idx] if idx < len(keyframe_urls) else None
            next_kf_url = (
                keyframe_urls[idx + 1] if idx + 1 < len(keyframe_urls) else None
            )

            # Fallback references for the rare scene that couldn't get a
            # keyframe (Seedream sensitivity-flagged). Multi-reference mode
            # gives the Seedance call SOME visual context.
            visible = list(scene.get("visible_characters") or [])
            speaker_label = scene.get("speaker_label", "narrator")
            if speaker_label != "narrator" and speaker_label not in visible:
                visible.insert(0, speaker_label)
            fallback_refs: list[str] = []
            if set_url:
                fallback_refs.append(set_url)
            fallback_refs.extend(
                merged_char_urls[lbl]
                for lbl in visible
                if lbl in merged_char_urls
            )

            sc_no = scene.get("scene_number")
            await _log(
                f"  Scene {sc_no} → Seedance i2v ({scene.get('duration_s')}s, "
                f"{'kf+last_kf' if (kf_url and next_kf_url) else ('kf only' if kf_url else 'multi-ref fallback')})"
            )
            try:
                clip_url = await render_scene(
                    scene=scene,
                    keyframe_url=kf_url,
                    next_keyframe_url=next_kf_url,
                    character_descriptions=char_descriptions,
                    fallback_reference_urls=fallback_refs,
                )
                await db.update_segment(
                    segment_id,
                    {
                        f"scene_status.{idx}": {
                            "scene_number": sc_no,
                            "status": "ready",
                            "clip_url": clip_url,
                        }
                    },
                )
                await _log(f"  Scene {sc_no} ✓ ready ({clip_url[:80]}…)")
                return clip_url
            except Exception as exc:
                log.warning(
                    "scene %d failed: %s — using silent placeholder",
                    sc_no, str(exc)[:300],
                )
                placeholder = await _silent_placeholder_clip()
                await db.update_segment(
                    segment_id,
                    {
                        f"scene_status.{idx}": {
                            "scene_number": sc_no,
                            "status": "placeholder",
                            "error": str(exc)[:200],
                            "clip_url": placeholder,
                        }
                    },
                )
                await _log(f"  Scene {sc_no} ✗ failed ({str(exc)[:120]}) — using silent placeholder", level="warn")
                return placeholder

        clip_urls = await asyncio.gather(
            *[_one_scene(i, sc) for i, sc in enumerate(scenes)]
        )
        await db.update_segment(
            segment_id,
            {"progress": 85, "clip_urls": list(clip_urls)},
        )
        await _stage_done(
            "Production",
            f"{len(clip_urls)} clip(s) generated",
        )

        # ------------------ STAGE 5: Editing -------------------------------
        await db.update_segment(segment_id, {"status": "stitching", "progress": 90})
        _abort_if_cancelled()
        await _stage_start(
            "Editing",
            "ffmpeg xfade blend + outro music + outro card",
        )
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        outro_music = os.path.join(repo_root, "data", "audio", "outro.mp3")
        if not os.path.exists(outro_music):
            outro_music = None
            await _log("No outro.mp3 found at data/audio/outro.mp3 — stitching without music")

        final_path = await stitch_film(
            list(clip_urls),
            outro_card=outro_card,
            outro_music_path=outro_music,
        )
        await _log(f"Stitched film: {final_path}")

        video_url = await db.upload_video(final_path, segment_id)
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
        await _stage_done("Editing", f"Published at {video_url}")
        log.info("drama segment %s ready at %s", segment_id, video_url)

    except _DramaCancelled:
        log.warning("[drama %s] pipeline aborted (user cancelled)", segment_id)
        cur = current_stage["name"]
        if cur:
            try:
                await _stage_skip(cur, "user cancelled")
            except Exception:
                pass
        await db.update_segment(
            segment_id,
            {
                "status": "cancelled",
                "error": "Cancelled by user",
            },
        )
        _CANCELLED_SEGMENTS.discard(segment_id)
        return
    except Exception as exc:
        log.exception("Short drama pipeline failed for segment %s", segment_id)
        # Mark whichever stage was in progress as failed so the UI shows it.
        cur = current_stage["name"]
        if cur:
            try:
                await _stage_fail(cur, str(exc)[:300])
            except Exception:
                pass
        await db.update_segment(
            segment_id, {"status": "failed", "error": str(exc)[:500]}
        )
        raise
