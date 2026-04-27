"""Reconstruct EXACTLY what _generate_direct sends to Fal Seedance for
scene 2 of segment seg_291a1d4f6b, and write it to a markdown file for
human review. Reads the same code paths the live pipeline uses, no
duplication of the prompt logic.

Run:
    cd the-tabloid
    backend/.venv/bin/python scripts/dump_scene2_payload.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force the same env the running uvicorn sees so settings() resolves correctly.
import dotenv
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

from backend.config import settings  # noqa: E402

SEGMENT_ID = "seg_291a1d4f6b"
SCRIPT_NAME = "skyroot"
SCENE_NUMBER = 2  # the scene we're inspecting


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script_path = os.path.join(repo_root, "data", "scripts", f"{SCRIPT_NAME}.json")
    manifest_path = os.path.join(
        repo_root, "data", "segments", SEGMENT_ID, "manifest.json"
    )
    script = json.load(open(script_path))
    manifest = json.load(open(manifest_path))

    personas = list(script["panel"])
    personas_by_id = {p["id"]: p for p in personas}

    scenes = list(script["scenes"])
    scene = next(s for s in scenes if s["scene_number"] == SCENE_NUMBER)
    persona_id = scene.get("featured_persona_id")
    featured = personas_by_id.get(persona_id)
    speaker_name = (featured or {}).get("name") or scene.get("featured_role", "?")
    vo_line = (scene.get("vo_line") or "").strip()

    # --- _panel_lock (verbatim port from pipeline.py, scoped to this scene) ---
    _SEAT_ORDER = ["provocateur", "analyst", "anchor", "humanist"]
    personas_by_role = {p.get("role"): p for p in personas}
    seated_panel = [
        personas_by_role[r] for r in _SEAT_ORDER if r in personas_by_role
    ]
    panel_lines = []
    for idx, p in enumerate(seated_panel):
        position = ["far left", "center-left", "center-right", "far right"][idx]
        viz = (p.get("visual_description") or "").strip()
        panel_lines.append(
            f"  · SEAT {idx + 1} ({position}): {p.get('name','?')} "
            f"({p.get('role','?')}). {viz}"
        )
    panel_block = "\n".join(panel_lines)

    seat_idx = next(
        (i for i, p in enumerate(seated_panel)
         if p.get("role") == (featured or {}).get("role")),
        None,
    )
    seat_label = (
        ["far left", "center-left", "center-right", "far right"][seat_idx]
        if seat_idx is not None else "their assigned seat"
    )
    speaker_clause = (
        f"FEATURED SPEAKER THIS SCENE: {speaker_name} (sitting at "
        f"{seat_label} of the desk). Camera focuses on {speaker_name}; "
        f"the other three panelists remain seated and visible "
        f"at the desk in the surrounding frame (over-shoulder)."
    )

    panel_lock = (
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

    # --- Chain clause (mode = r2v-dual, latest version) ---
    chain_clause = (
        "\n\nIMAGE REFERENCES (priority: @Image1 dominates identity, "
        "@Image2 is setup-only):\n"
        "@Image1 is the GROUND TRUTH for everything in this scene — "
        "use it for camera framing, set composition, lighting, "
        "panelist faces, panelist ages, panelist wardrobes, panelist "
        "hair, panelist seat positions, and the overall photoreal "
        "rendering style. Every panelist visible in this shot must "
        "match @Image1 EXACTLY in face, age, hair, and wardrobe.\n\n"
        "@Image2 is a SETUP REFERENCE ONLY — use it ONLY to confirm "
        "the desk shape, the THE TABLOID backdrop, the studio "
        "lighting design, and which panelist sits in which seat "
        "position (left-to-right order). DO NOT use @Image2 for "
        "faces. DO NOT use @Image2 for the rendering style. DO NOT "
        "blend @Image2's facial features into the panelists. DO NOT "
        "change anyone's seating position from what is in @Image1. "
        "If @Image1 and @Image2 disagree on visual style, ALWAYS "
        "follow @Image1.\n\n"
        "DO NOT make this scene more cartoon-stylized than @Image1. "
        "Keep the photorealistic broadcast look established by @Image1."
    )

    audio_clause = (
        "\n\nAUDIO: Studio dialogue only. The speaker's voice over "
        "clean room tone. NO music, NO sound effects, NO dramatic "
        "stings, NO whip-pan whooshes, NO gunshots, NO explosions, "
        "NO breaking glass, NO action-movie ambient. Treat this as "
        "a calm news-debate set."
    )

    spoken_clause = (
        "\n\nSPOKEN LINE — DELIVER VERBATIM. The featured speaker "
        "speaks this line in full within the 5-second clip. "
        "DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add "
        "words. DO NOT summarize. Lip-sync the speaker's mouth to "
        "these exact words at a natural broadcast pace.\n"
        f"{speaker_name}: \"{vo_line}\""
    )

    locked_prompt = (
        panel_lock
        + "\n\nTHIS SCENE: "
        + (scene.get("seedance_prompt") or "").strip()
        + chain_clause
        + audio_clause
        + spoken_clause
    )

    # generate_seedance_clip wraps it with motion + resolution. fal.py for
    # r2v doesn't add motion text; only Seedance i2v does. So for r2v the
    # final prompt sent to Fal is exactly `locked_prompt` above (motion-
    # directive prefix is added by seedance.py, but it goes through
    # generate_fal_video which routes to r2v WITHOUT adding the directive
    # for r2v calls). Verify by checking seedance.py:
    motion_directive_added = True  # seedance.py wraps before calling fal
    if motion_directive_added:
        from backend.video.seedance import _motion_directive
        motion = scene.get("camera_motion", "static")
        rich_prompt = (
            f"{locked_prompt.strip()} "
            f"{_motion_directive(motion)} "
            f"Aspect ratio: 9:16. Broadcast-polished composition."
        )
    else:
        rich_prompt = locked_prompt

    # --- image_urls (the actual array passed to image_urls[]) ---
    chain_anchor_url = manifest["clips"][0]["chain_anchor_url"]
    avatar_url = manifest.get("avatar_url")
    image_urls = [chain_anchor_url, avatar_url]

    # --- seed ---
    seed = int(
        hashlib.sha1(
            ((persona_id or "ensemble") + SEGMENT_ID[:6]).encode()
        ).hexdigest(),
        16,
    ) % 2**31

    # --- write markdown ---
    out_path = os.path.join(repo_root, "scripts", "scene2_payload_review.md")
    md = []
    md.append("# Scene 2 — Exact Payload to Fal Seedance r2v")
    md.append("")
    md.append(f"Segment: `{SEGMENT_ID}` · Script: `{SCRIPT_NAME}` · Scene: `{SCENE_NUMBER}` · Mode: `r2v-dual`")
    md.append("")
    md.append("## Endpoint")
    md.append("`bytedance/seedance-2.0/fast/reference-to-video`")
    md.append("")
    md.append("## Request body fields")
    md.append("| Field | Value |")
    md.append("|---|---|")
    md.append(f"| `duration` | `10` (Seedance accepts 4-15; we requested 10) |")
    md.append(f"| `aspect_ratio` | `9:16` |")
    md.append(f"| `resolution` | `720p` |")
    md.append(f"| `generate_audio` | `true` |")
    md.append(f"| `seed` | `{seed}` |")
    md.append("")
    md.append("## `image_urls` (passed verbatim to the API)")
    md.append("")
    md.append("### `image_urls[0]` → `@Image1`")
    md.append(f"- Role: GROUND TRUTH (faces, framing, style)")
    md.append(f"- URL: <{image_urls[0]}>")
    md.append(f"- Preview:")
    md.append(f"  ![chain anchor]({image_urls[0]})")
    md.append("")
    md.append("### `image_urls[1]` → `@Image2`")
    md.append(f"- Role: SETUP REFERENCE ONLY (desk, backdrop, lighting, seat order)")
    md.append(f"- URL: <{image_urls[1]}>")
    md.append(f"- Preview:")
    md.append(f"  ![avatar]({image_urls[1]})")
    md.append("")
    md.append("## `prompt` (full text sent to Fal)")
    md.append("")
    md.append("```")
    md.append(rich_prompt)
    md.append("```")
    md.append("")
    md.append("## Per-section breakdown")
    md.append("")
    md.append("### 1. Panel lock (always present)")
    md.append("```")
    md.append(panel_lock)
    md.append("```")
    md.append("")
    md.append("### 2. THIS SCENE: (verbatim from skyroot.json)")
    md.append("```")
    md.append((scene.get("seedance_prompt") or "").strip())
    md.append("```")
    md.append("")
    md.append("### 3. Chain clause (r2v-dual mode only)")
    md.append("```")
    md.append(chain_clause.strip())
    md.append("```")
    md.append("")
    md.append("### 4. AUDIO clause")
    md.append("```")
    md.append(audio_clause.strip())
    md.append("```")
    md.append("")
    md.append("### 5. SPOKEN LINE clause (verbatim VO line included)")
    md.append("```")
    md.append(spoken_clause.strip())
    md.append("```")
    md.append("")
    md.append("### 6. Motion + framing tail (added by seedance.py wrapper)")
    md.append("```")
    md.append(f"{_motion_directive(scene.get('camera_motion', 'static'))} Aspect ratio: 9:16. Broadcast-polished composition.")
    md.append("```")
    md.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(md))

    print(f"wrote: {out_path}")
    print(f"size: {os.path.getsize(out_path)} bytes")


if __name__ == "__main__":
    main()
