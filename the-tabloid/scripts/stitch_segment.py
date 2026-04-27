"""ffmpeg-concat all the per-scene mp4s of a direct-mode segment into one
final 2-min episode mp4. Order follows the manifest's clips array.

Run:
    cd the-tabloid
    backend/.venv/bin/python scripts/stitch_segment.py [SEGMENT_ID]

Default SEGMENT_ID = seg_03a618cd9f (the Skyroot run).

Output path: data/videos/{segment_id}_full.mp4 (also accessible via the
backend's /api/videos route as {segment_id}_full.mp4 if that route is
extended; otherwise just play the local file directly).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    seg_id = sys.argv[1] if len(sys.argv) > 1 else "seg_03a618cd9f"

    seg_dir = os.path.join(REPO_ROOT, "data", "segments", seg_id)
    manifest_path = os.path.join(seg_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"manifest not found: {manifest_path}")
        sys.exit(1)

    manifest = json.load(open(manifest_path))
    clips = manifest.get("clips") or []
    if not clips:
        print("manifest has no clips")
        sys.exit(1)

    # Build an ordered list of local mp4 paths from the manifest.
    # File naming: scene_{slug}.mp4 where slug is "09" for int 9, "9b" for str "9b".
    def _slug(scene_number) -> str:
        try:
            return f"{int(scene_number):02d}"
        except (ValueError, TypeError):
            return str(scene_number)

    print("=" * 70)
    print(f"Stitching {seg_id} — {len(clips)} clips")
    print("=" * 70)
    mp4_paths: list[str] = []
    total_dur = 0
    for c in clips:
        sn = c.get("scene_number")
        slug = _slug(sn)
        path = os.path.join(seg_dir, f"scene_{slug}.mp4")
        if c.get("status") != "ready":
            print(f"  [SKIP] s{sn}: status={c.get('status')}")
            continue
        if not os.path.exists(path):
            print(f"  [WARN] s{sn}: file missing — {path}")
            continue
        size_mb = os.path.getsize(path) / (1024 * 1024)
        # Probe duration via ffprobe.
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of",
                 "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, check=True,
            )
            dur = float(r.stdout.strip() or 0)
        except Exception:
            dur = 0
        total_dur += dur
        print(f"  [ OK ] s{sn:>3}: {path.split('/')[-1]:18s}  {dur:5.1f}s  {size_mb:5.1f}MB")
        mp4_paths.append(path)

    print()
    print(f"Total clips to concat: {len(mp4_paths)}")
    print(f"Sum of clip durations: {total_dur:.1f}s ({total_dur/60:.2f} min)")
    print()

    if not mp4_paths:
        print("nothing to stitch")
        sys.exit(1)

    # Write the concat manifest list.
    out_dir = os.path.join(REPO_ROOT, "data", "videos")
    os.makedirs(out_dir, exist_ok=True)
    list_path = os.path.join(seg_dir, "concat_list.txt")
    out_path = os.path.join(out_dir, f"{seg_id}_full.mp4")

    with open(list_path, "w") as f:
        for p in mp4_paths:
            esc = p.replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    print(f"wrote concat list: {list_path}")

    # First try fast stream-copy concat. If clips have identical codecs
    # (likely, all from same Seedance endpoint), this is near-instant and
    # lossless. Falls back to re-encode if -c copy fails.
    print(f"running ffmpeg concat → {out_path}")
    cmd_copy = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", "-movflags", "+faststart",
        out_path,
    ]
    try:
        subprocess.run(cmd_copy, check=True, capture_output=True, text=True)
        mode = "stream-copy (fast, lossless)"
    except subprocess.CalledProcessError as exc:
        print(f"  -c copy failed, re-encoding (slower but reliable)...")
        print(f"  reason: {(exc.stderr or '')[:300]}")
        cmd_reenc = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(cmd_reenc, check=True)
        mode = "re-encoded (libx264 / aac)"

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        print("output file empty — concat failed")
        sys.exit(2)

    final_size_mb = os.path.getsize(out_path) / (1024 * 1024)
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", out_path],
            capture_output=True, text=True, check=True,
        )
        final_dur = float(r.stdout.strip() or 0)
    except Exception:
        final_dur = 0

    print()
    print("=" * 70)
    print("STITCH COMPLETE")
    print("=" * 70)
    print(f"Output: {out_path}")
    print(f"Mode:   {mode}")
    print(f"Size:   {final_size_mb:.1f} MB")
    print(f"Duration: {final_dur:.1f}s ({final_dur/60:.2f} min)")
    print()
    print(f"Local URL: file://{out_path}")
    print(f"Or copy to data/videos/ and serve via /api/videos/{seg_id}_full.mp4 route")


if __name__ == "__main__":
    main()
