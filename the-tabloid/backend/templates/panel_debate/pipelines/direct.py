"""Direct mode — short-circuited production pipeline.

The user's mental model:
  story_selector → casting → script → set → director → executor → editor

Direct mode short-circuits the agents on the LEFT (story/casting/script/
set/director) by reading their outputs out of a hand-authored script JSON
(`data/scripts/<name>.json`). The agents on the RIGHT (executor + editor)
run identically to production. That's why this file produces typed
`ShotPlan` artifacts — the same artifact a future Director agent will
emit dynamically — and feeds them through the same executor +
chain-anchor + persistence loop.

Concretely, the script JSON pre-fills:
  - story    (`story` block: headline, source, url, ...)
  - cast     (`panel`: 4 panelists with id/name/role/voice/visual_description)
  - set      (`avatar_path`: PNG → uploaded to Fal once, cached)
  - script   (`scenes[]`: vo_line, seedance_prompt, camera_motion,
              featured_persona_id, duration)
  - director's per-scene intent (`scenes[i].seedance_prompt` is the motion
              direction; the alternating-anchor frame logic happens here in
              `_compose_shot_plan`)

Production mode swaps each of these with the live agent. The executor
loop in `_execute_shot_plan` doesn't change — that's the point.

Failure mode:
  Any individual Seedance call that fails halts the batch (chain broken).
  Already-rendered scenes are preserved in the manifest; user re-fires
  after fixing the cause. Scenes after the failed one are marked 'skipped'.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from ....config import settings
from ....db.firestore import FirestoreClient
from ....sdk.providers.seedance import generate_seedance_clip
from ....sdk.providers.seedream import ensure_master_stage_frame
from ....sdk.types import CastMemberRef, ShotPlan
from ._helpers import (
    _COST_BY_MODE,
    _COST_I2V,
    _I2V_MODEL,
    _SPEAKER_VISUAL_ID,
    _T2V_MODEL,
    _initial_clips_state,
    _prep_chain_anchor,
    _resolve_local_avatar,
    _save_clip_locally,
    _write_segment_manifest,
    load_segment_manifest,
)

log = logging.getLogger(__name__)


_SEAT_ORDER = ["provocateur", "humanist", "anchor", "analyst"]
_SEAT_LABELS = ["far left", "center-left", "center-right", "far right"]


def _seated_panel(personas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order panelists left-to-right at the desk, matching the avatar PNG."""
    by_role = {p.get("role"): p for p in personas}
    return [by_role[r] for r in _SEAT_ORDER if r in by_role]


def _seat_label_for(seated_panel: list[dict[str, Any]], featured: dict | None) -> str:
    if not featured:
        return "their assigned seat"
    role = featured.get("role")
    seat_idx = next((i for i, p in enumerate(seated_panel) if p.get("role") == role), None)
    return _SEAT_LABELS[seat_idx] if seat_idx is not None else "their assigned seat"


def _t2v_panel_lock(seated_panel: list[dict], featured: dict[str, Any] | None) -> str:
    """Verbose panel + cast + wardrobe lock for the t2v fallback path
    (when avatar generation failed and there's no image to anchor identity).
    Rare on direct mode."""
    panel_lines: list[str] = []
    for idx, p in enumerate(seated_panel):
        viz = (p.get("visual_description") or "").strip()
        panel_lines.append(
            f"  · SEAT {idx + 1} ({_SEAT_LABELS[idx]}): {p.get('name','?')} "
            f"({p.get('role','?')}). {viz}"
        )
    panel_block = "\n".join(panel_lines)

    speaker = (featured or {}).get("name") if featured else None
    if speaker:
        seat_label = _seat_label_for(seated_panel, featured)
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
        "STYLE: Broadcast-polished, composed.\n\n"
        "BRAND LOCK — backdrop text reads 'THE TABLOID' (capital "
        "letters, exactly that spelling)."
    )


def _i2v_lean_prompt(
    *,
    seated_panel: list[dict[str, Any]],
    featured: dict[str, Any] | None,
    persona_id: str,
    speaker_name: str,
    motion_desc: str,
    vo_line: str,
    mode_tag: str,
    native_audio: bool,
) -> str:
    """Lean motion-only i2v prompt (~150-200 words). Identity comes from
    the input image; prompt only carries motion + dialogue + a strong
    speaker locator so the model focuses the right person.

    Re-describing what's already in the image makes the model second-guess
    what it can see and pushes outputs toward whatever the description
    sounds like (e.g. specific photoreal anatomy descriptions push
    outputs toward photoreal even when the input is stylized)."""
    seat_label = _seat_label_for(seated_panel, featured)
    visual_id = _SPEAKER_VISUAL_ID.get(persona_id or "", "")
    visual_id_clause = (
        f" Visual identifier (find this person in the input image): "
        f"{visual_id}."
        if visual_id else ""
    )
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
    return (
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


def _t2v_fallback_prompt(
    *,
    seated_panel: list[dict[str, Any]],
    featured: dict[str, Any] | None,
    motion_desc: str,
    speaker_name: str,
    vo_line: str,
    native_audio: bool,
) -> str:
    """T2V fallback prompt: needs the verbose panel + cast lock because
    there's no image to anchor identity. Rare path on direct mode — only
    hits when avatar generation failed."""
    out = (
        _t2v_panel_lock(seated_panel, featured)
        + "\n\nTHIS SCENE: " + motion_desc
        + "\n\nAUDIO: Studio dialogue only. The speaker's voice over "
        "clean room tone. NO music, NO sound effects, NO dramatic "
        "stings, NO whip-pan whooshes, NO gunshots, NO explosions, "
        "NO breaking glass, NO action-movie ambient. Treat this as "
        "a calm news-debate set."
    )
    if native_audio and vo_line:
        out += (
            "\n\nSPOKEN LINE — DELIVER VERBATIM. The featured speaker "
            "speaks this line in full within the 5-second clip. "
            "DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add "
            "words. DO NOT summarize. Lip-sync the speaker's mouth to "
            "these exact words at a natural broadcast pace.\n"
            f"{speaker_name}: \"{vo_line}\""
        )
    return out


def _compose_shot_plan(
    *,
    scene: dict[str, Any],
    scene_index: int,
    personas_by_id: dict[str, dict[str, Any]],
    seated_panel: list[dict[str, Any]],
    avatar_url: str | None,
    prior_chain_url: str | None,
    segment_id: str,
    native_audio: bool,
) -> ShotPlan:
    """Produce the typed director artifact for ONE scene.

    This is the "director's vision" in direct mode: the alternating-anchor
    pattern, frame anchor selection, model choice, and prompt composition
    — all decided here, output as a typed ShotPlan the executor consumes.

    Endpoint-locked alternating-anchor pattern:
      - Scene 1: image_url=avatar, no end_image_url → free landing
      - Scene 2 (even): image_url=scene 1's last frame, end_image_url=avatar
      - Scene 3 (odd):  image_url=scene 2's last frame (=avatar), no end
      - Scene 4 (even): image_url=scene 3's last frame, end_image_url=avatar
      - … and so on. Every even scene re-anchors to avatar at the end,
        so every odd scene STARTS on the avatar (moderator-safe).

    Parity is by scene_index+1 so inserted sub-scenes ("9b") still alternate.
    """
    persona_id = scene.get("featured_persona_id") or ""
    featured = personas_by_id.get(persona_id) if persona_id else None
    speaker_name = (featured or {}).get("name") or scene.get("featured_role", "?")
    vo_line = (scene.get("vo_line") or "").strip()
    motion_desc = (scene.get("seedance_prompt") or "").strip()

    is_even_position = (scene_index + 1) % 2 == 0
    chosen_end_image: str | None = None
    if scene_index == 0 and avatar_url:
        chosen_model = _I2V_MODEL
        first_frame: str | None = avatar_url
        mode_tag = "i2v-avatar"
    elif prior_chain_url:
        chosen_model = _I2V_MODEL
        first_frame = prior_chain_url
        if avatar_url and is_even_position:
            chosen_end_image = avatar_url
            mode_tag = "i2v-locked"
        else:
            mode_tag = "i2v-chained"
    else:
        chosen_model = _T2V_MODEL
        first_frame = None
        mode_tag = "t2v"

    if first_frame:
        prompt = _i2v_lean_prompt(
            seated_panel=seated_panel,
            featured=featured,
            persona_id=persona_id,
            speaker_name=speaker_name,
            motion_desc=motion_desc,
            vo_line=vo_line,
            mode_tag=mode_tag,
            native_audio=native_audio,
        )
    else:
        prompt = _t2v_fallback_prompt(
            seated_panel=seated_panel,
            featured=featured,
            motion_desc=motion_desc,
            speaker_name=speaker_name,
            vo_line=vo_line,
            native_audio=native_audio,
        )

    seed = int(
        hashlib.sha1(
            ((persona_id or "ensemble") + segment_id[:6]).encode()
        ).hexdigest(),
        16,
    ) % 2**31

    return ShotPlan(
        scene_id=f"{segment_id}:scene_{scene.get('scene_number', scene_index + 1)}",
        scene_number=scene.get("scene_number", scene_index + 1),
        speaker=CastMemberRef(persona_id) if persona_id else None,
        start_image_url=first_frame,
        end_image_url=chosen_end_image,
        motion=motion_desc,
        framing="medium_close_up",
        model=chosen_model,
        mode_tag=mode_tag,
        prompt=prompt,
        spoken_line=vo_line,
        duration_seconds=int(scene.get("duration", 5)),
        aspect_ratio="9:16",
        seed=seed,
    )


async def _execute_shot_plan(
    plan: ShotPlan, *, segment_id: str, camera_motion: str
) -> tuple[str | None, str | None, str | None]:
    """Run the director's plan: call Seedance, persist the clip locally,
    return (public_url, remote_url, local_mp4_path). Raises on Seedance
    failure so the caller can mark the scene 'failed' and halt the batch.
    """
    clip_url = await generate_seedance_clip(
        prompt=plan.prompt,
        camera_motion=camera_motion,
        duration=plan.duration_seconds,
        aspect_ratio=plan.aspect_ratio,
        first_frame_image=plan.start_image_url,
        reference_image=None,
        seed=plan.seed,
        model_override=plan.model,
        end_image_url=plan.end_image_url,
    )
    public_url, local_path = await _save_clip_locally(
        segment_id, plan.scene_number, clip_url
    )
    return public_url or clip_url, clip_url, local_path


async def _generate_direct(
    segment_id: str,
    channel: str,
    script_data: dict[str, Any],
    *,
    start_scene: int = 1,
    end_scene: int | None = None,
) -> None:
    """Take a hand-authored script JSON (story + panel + N scenes with
    verbatim VO), produce a ShotPlan per scene, run Seedance, persist
    per-scene URLs.

    Batched generation: pass start_scene + end_scene (1-indexed inclusive)
    to render a slice. The on-disk manifest at data/segments/{sid}/
    manifest.json is the source of truth — preserves state across uvicorn
    restarts and across batches. Successfully-rendered clips are downloaded
    from Fal CDN to data/segments/{sid}/scene_NN.mp4 and re-served locally
    so we don't lose them when Fal's CDN URLs expire.
    """
    db = FirestoreClient()
    personas = list(script_data.get("panel") or [])
    personas_by_id = {p["id"]: p for p in personas}
    scenes = list(script_data.get("scenes") or [])
    story = script_data.get("story") or {}

    if not scenes:
        await db.update_segment(
            segment_id, {"status": "failed", "error": "direct script has no scenes"}
        )
        return
    if not personas:
        await db.update_segment(
            segment_id, {"status": "failed", "error": "direct script has no panel"}
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

    # Set agent (short-circuited): resolve the episode avatar URL that
    # scene 1 will use as first_frame.
    #   1. script_data.avatar_path: a local image path (relative to repo
    #      root). Uploaded to Fal once, URL cached in a sidecar.
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

    seated_panel = _seated_panel(personas)
    native_audio = (
        settings().video_provider == "fal"
        and "seedance" in settings().fal_video_model
    )

    for batch_pos, i in enumerate(target_indices):
        scene = scenes[i]

        # Director (short-circuited): produce the typed ShotPlan from the
        # script JSON + accumulated chain-anchor state.
        prior_chain_url: str | None = None
        if i > 0:
            prior = clips_state[i - 1]
            if prior.get("status") == "ready":
                prior_chain_url = prior.get("chain_anchor_url")

        plan = _compose_shot_plan(
            scene=scene,
            scene_index=i,
            personas_by_id=personas_by_id,
            seated_panel=seated_panel,
            avatar_url=avatar_url,
            prior_chain_url=prior_chain_url,
            segment_id=segment_id,
            native_audio=native_audio,
        )

        speaker_name = (
            (personas_by_id.get(plan.speaker.id) if plan.speaker else None) or {}
        ).get("name") or scene.get("featured_role", "?")
        log.info(
            "Direct scene %s (%s) — mode=%s, first_frame=%s",
            plan.scene_number, speaker_name, plan.mode_tag,
            "set" if plan.start_image_url else "none",
        )

        # Executor: run the plan.
        local_mp4_path: str | None = None
        try:
            public_url, remote_url, local_mp4_path = await _execute_shot_plan(
                plan,
                segment_id=segment_id,
                camera_motion=scene.get("camera_motion", "static"),
            )
            clips_state[i]["video_url"] = public_url
            clips_state[i]["remote_url"] = remote_url
            clips_state[i]["status"] = "ready"
            clips_state[i]["error"] = None
            clips_state[i]["mode"] = plan.mode_tag
            clips_state[i]["cost_usd"] = _COST_BY_MODE.get(plan.mode_tag, _COST_I2V)
        except Exception as exc:
            log.warning(
                "Direct scene %s (%s, mode=%s) failed: %s",
                plan.scene_number, speaker_name, plan.mode_tag, str(exc)[:300],
            )
            clips_state[i]["status"] = "failed"
            clips_state[i]["error"] = str(exc)[:500]
            clips_state[i]["mode"] = plan.mode_tag

        # Chain prep: extract this scene's last frame, upload, store the
        # URL on this scene's clip state for the next scene's ShotPlan to
        # pick up. Skip for the last scene + failed scenes.
        if (
            clips_state[i]["status"] == "ready"
            and local_mp4_path
            and i + 1 < len(scenes)
        ):
            try:
                anchor_url = await _prep_chain_anchor(
                    segment_id, plan.scene_number, local_mp4_path
                )
                if anchor_url:
                    clips_state[i]["chain_anchor_url"] = anchor_url
                    log.info(
                        "Chain anchor ready for scene %s",
                        plan.scene_number,
                    )
                else:
                    log.warning(
                        "Chain anchor prep returned None for scene %s — "
                        "next scene falls back to t2v",
                        plan.scene_number,
                    )
            except Exception as exc:
                log.warning(
                    "Chain anchor prep crashed for scene %s: %s",
                    plan.scene_number, str(exc)[:200],
                )

        # Halt-on-failure: chained pipeline depends on prior success.
        # If this scene failed, every subsequent scene in the batch is
        # wasted spend (no chain anchor available). Mark remaining as
        # 'skipped', persist, and break the loop.
        if clips_state[i]["status"] == "failed":
            log.warning(
                "Halting batch at scene %s (%s) — chain broken. "
                "Remaining %d scenes marked 'skipped'.",
                plan.scene_number, plan.mode_tag,
                len(target_indices) - batch_pos - 1,
            )
            for skip_idx in target_indices[batch_pos + 1:]:
                if clips_state[skip_idx]["status"] in ("queued", "pending"):
                    clips_state[skip_idx]["status"] = "skipped"
                    clips_state[skip_idx]["error"] = (
                        f"Skipped: prior scene {plan.scene_number} failed "
                        f"({plan.mode_tag}); chain broken — re-fire after fix."
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

        # Persist after every scene — manifest is durable, segment doc is
        # the live UI feed. Both stay in sync.
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
    final_status = (
        "ready" if (queued + skipped) == 0
        else ("partial" if failed == 0 else "failed_chain")
    )
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
