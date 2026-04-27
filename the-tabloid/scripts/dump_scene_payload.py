"""Reconstruct any scene's exact prompt for review. Usage:
    backend/.venv/bin/python scripts/dump_scene_payload.py SEG_ID SCENE_NUMBER
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import dotenv
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.video.seedance import _motion_directive  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main():
    seg_id = sys.argv[1] if len(sys.argv) > 1 else "seg_03a618cd9f"
    scene_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    script = json.load(open(os.path.join(REPO_ROOT, "data", "scripts", "skyroot.json")))
    manifest = json.load(open(os.path.join(REPO_ROOT, "data", "segments", seg_id, "manifest.json")))

    personas = list(script["panel"])
    by_id = {p["id"]: p for p in personas}
    scenes = list(script["scenes"])
    scene = next(s for s in scenes if s["scene_number"] == scene_n)
    persona_id = scene.get("featured_persona_id")
    featured = by_id.get(persona_id)
    speaker_name = (featured or {}).get("name") or scene.get("featured_role", "?")
    vo_line = (scene.get("vo_line") or "").strip()

    # Reconstruct _panel_lock (mirrors pipeline.py)
    SEAT_ORDER = ["provocateur", "analyst", "anchor", "humanist"]
    by_role = {p.get("role"): p for p in personas}
    seated = [by_role[r] for r in SEAT_ORDER if r in by_role]
    panel_lines = []
    for idx, p in enumerate(seated):
        pos = ["far left", "center-left", "center-right", "far right"][idx]
        viz = (p.get("visual_description") or "").strip()
        panel_lines.append(f"  · SEAT {idx + 1} ({pos}): {p.get('name','?')} ({p.get('role','?')}). {viz}")
    panel_block = "\n".join(panel_lines)

    seat_idx = next(
        (i for i, p in enumerate(seated) if p.get("role") == (featured or {}).get("role")),
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

    # NEW style-neutral chain clause (post-refactor)
    chain_clause = (
        "\n\nCHAIN: This shot continues directly from the input image "
        "(a held still of THE TABLOID news desk). Set, lighting, "
        "wardrobe, body positions, panelist faces, and panelist "
        "arrangement are ALREADY established by that image. Match "
        "the input image's visual style EXACTLY — do NOT make it "
        "more photoreal or more stylized than the input. Preserve "
        "all four panelists' faces from the input image."
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
        panel_lock + "\n\nTHIS SCENE: " + (scene.get("seedance_prompt") or "").strip()
        + chain_clause + audio_clause + spoken_clause
    )
    motion = scene.get("camera_motion", "static")
    rich_prompt = (
        f"{locked_prompt.strip()} {_motion_directive(motion)} "
        f"Aspect ratio: 9:16. Broadcast-polished composition."
    )

    even = scene_n % 2 == 0
    print("=" * 70)
    print(f"SCENE {scene_n} EXACT PROMPT — {seg_id}")
    print("=" * 70)
    print(f"speaker:      {speaker_name}")
    print(f"camera:       {motion}")
    print(f"duration:     {scene.get('duration')}s")
    print(f"mode:         {'i2v-locked (even)' if even else 'i2v-chained (odd)'}")
    print(f"image_url:    prior chain anchor")
    print(f"end_image_url: {'avatar' if even else 'none (free)'}")
    print()
    print("=" * 70)
    print("FULL PROMPT TEXT:")
    print("=" * 70)
    print(rich_prompt)


if __name__ == "__main__":
    main()
