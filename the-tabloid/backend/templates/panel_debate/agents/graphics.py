"""Broadcast graphics package for THE TABLOID.

Four overlay layers, composited over the assembled episode in a single
ffmpeg pass (Strategy A from the spec):

  1. Brand bug        — persistent corner logo
  2. Segment title    — one-shot at episode start (story name, fades)
  3. Lower thirds     — panelist name+role tag at each scene start
  4. Captions (SRT)   — burned-in spoken dialogue, timed to scene durations

Asset resolution:
  - If `assets/graphics/<rel>` exists at repo root, use it (designer-supplied).
  - Else generate to `<work_dir>/.graphics/<rel>` via Pillow and use that.

The auto-generated assets aren't a substitute for hand-designed graphics
— they're a "looks like a broadcast" fallback so the pipeline ships
end-to-end without anyone hand-rendering 4 panelists' lower thirds.

Filter composition is generated programmatically from the manifest's
clips array — one overlay enable-window per scene, timing computed
from actual clip durations and the xfade duration.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


# THE TABLOID brand palette — matches the player CSS.
_BRAND_RED = (250, 62, 62)        # #FA3E3E
_BRAND_BLACK = (11, 11, 15)       # #0B0B0F
_BRAND_GOLD = (255, 210, 74)      # #FFD24A — for role tags
_TEXT_WHITE = (255, 255, 255)
_TEXT_DIM = (197, 197, 207)

# Output canvas dimensions (must match the blended episode).
_VIDEO_W = 720
_VIDEO_H = 1280


# -- Font resolution --------------------------------------------------------

# Tried in order. Pillow falls back to default bitmap font if none load.
_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
_FONT_CANDIDATES_REGULAR = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Geneva.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _resolve_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Find the first available system font and return it at the requested
    size. Bitmap default fallback if nothing works."""
    candidates = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    for path in candidates:
        if os.path.exists(path):
            try:
                # Helvetica.ttc is a TTC collection — index 1 is bold for
                # Helvetica family. Try a few indices for collection files.
                if path.endswith(".ttc"):
                    return ImageFont.truetype(path, size, index=1 if bold else 0)
                return ImageFont.truetype(path, size)
            except Exception as exc:
                log.debug("font %s failed at size %d: %s", path, size, exc)
                continue
    log.warning("no usable system font found, using Pillow default bitmap")
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Width, height of a text string in pixels. Pillow's textbbox returns
    (left, top, right, bottom)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


# -- Pillow asset generation ------------------------------------------------

def make_brand_bug_png(out_path: str, *, width: int = 140) -> None:
    """Generate the THE TABLOID brand bug — small red pill with the show
    name in white. Transparent background, rounded corners, fits in
    the top-right corner of a 720x1280 frame."""
    height = 36
    pad_x = 12
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rect — pill shape.
    draw.rounded_rectangle(
        [(0, 0), (width - 1, height - 1)],
        radius=height // 2,
        fill=_BRAND_RED + (230,),  # ~90% opaque
    )

    font = _resolve_font(size=14, bold=True)
    text = "THE TABLOID"
    tw, th = _text_size(draw, text, font)
    draw.text(
        ((width - tw) / 2, (height - th) / 2 - 2),
        text,
        font=font,
        fill=_TEXT_WHITE,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")


def make_lower_third_png(
    out_path: str,
    *,
    name: str,
    width: int = 480,
    height: int = 64,
) -> None:
    """Generate a panelist's name tag — just the name, on a translucent
    dark pill with a red accent bar. No role/culture — keeping the
    chyron minimal so it doesn't compete with the speaker on screen."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Translucent dark backplate, slight rounding.
    draw.rounded_rectangle(
        [(0, 0), (width - 1, height - 1)],
        radius=4,
        fill=_BRAND_BLACK + (200,),  # ~80% opaque
    )
    # Red accent bar on the left edge.
    draw.rectangle([(0, 0), (6, height - 1)], fill=_BRAND_RED)

    name_font = _resolve_font(size=26, bold=True)
    pad_left = 22
    text = name.upper()
    _, th = _text_size(draw, text, name_font)
    draw.text((pad_left, (height - th) // 2 - 2), text, font=name_font, fill=_TEXT_WHITE)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")


def make_caption_png(
    out_path: str,
    *,
    text: str,
    width: int = 660,
    font_size: int = 22,
    line_height: int = 30,
    horiz_padding: int = 24,
    vert_padding: int = 14,
    max_lines: int = 3,
) -> tuple[int, int]:
    """Generate a transparent caption strip PNG with the dialogue line
    wrapped, on a semi-transparent dark backplate.

    We pre-render captions as PNGs because ffmpeg's `subtitles` filter
    requires libass, which Homebrew's ffmpeg ships without. Returns the
    final (width, height) of the saved PNG so the caller can position it.
    """
    font = _resolve_font(size=font_size, bold=False)
    # Probe wrapping width before we know height.
    probe_img = Image.new("RGBA", (1, 1))
    probe_draw = ImageDraw.Draw(probe_img)
    inner_w = width - 2 * horiz_padding
    lines = _wrap_text(text, font, probe_draw, max_width=inner_w, max_lines=max_lines)

    height = vert_padding * 2 + line_height * len(lines)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Semi-transparent black backplate, slightly rounded.
    draw.rounded_rectangle(
        [(0, 0), (width - 1, height - 1)],
        radius=6,
        fill=(0, 0, 0, 175),  # ~70% opaque
    )
    # Render each line centered horizontally.
    for i, line in enumerate(lines):
        lw, _ = _text_size(draw, line, font)
        x = (width - lw) // 2
        y = vert_padding + i * line_height
        # Soft drop shadow for legibility on busy backgrounds.
        draw.text((x + 1, y + 1), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=_TEXT_WHITE)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")
    return width, height


def make_segment_title_png(
    out_path: str,
    *,
    headline: str,
    width: int = 720,
    height: int = 280,
) -> None:
    """Generate a full-width segment title card.

    Layout:
      "BREAKING" eyebrow (red, 16pt bold) above
      Story headline wrapped to 2 lines max (32pt bold white)
      Translucent dark backplate behind everything for contrast.

    Drops onto the video at t=2..5 with a fade per the editor pipeline.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Backplate gradient effect via a centered solid block (simplest).
    draw.rectangle(
        [(0, 30), (width - 1, height - 30)],
        fill=_BRAND_BLACK + (220,),
    )
    # Top + bottom red accents.
    draw.rectangle([(0, 30), (width - 1, 38)], fill=_BRAND_RED)
    draw.rectangle([(0, height - 38), (width - 1, height - 30)], fill=_BRAND_RED)

    eyebrow_font = _resolve_font(size=18, bold=True)
    headline_font = _resolve_font(size=34, bold=True)

    eyebrow = "BREAKING · THE TABLOID"
    ew, eh = _text_size(draw, eyebrow, eyebrow_font)
    draw.text(((width - ew) // 2, 60), eyebrow, font=eyebrow_font, fill=_BRAND_RED)

    # Wrap headline to ≤2 lines naive-greedy.
    headline_clean = headline.upper().strip()
    lines = _wrap_text(headline_clean, headline_font, draw, max_width=width - 80, max_lines=2)
    line_height = 44
    block_height = line_height * len(lines)
    y0 = 110 + max(0, (120 - block_height) // 2)
    for i, line in enumerate(lines):
        lw, _ = _text_size(draw, line, headline_font)
        draw.text(((width - lw) // 2, y0 + i * line_height), line, font=headline_font, fill=_TEXT_WHITE)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG")


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
    *,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Greedy word-wrap. If the text overflows max_lines, last line is
    truncated with an ellipsis."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_size(draw, trial, font)[0] <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if not lines:
        return [text[:30]]
    # Truncate last line if there are more words we couldn't fit.
    consumed = sum(len(l.split()) for l in lines)
    if consumed < len(words) and lines:
        last = lines[-1]
        while _text_size(draw, last + "…", font)[0] > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


# -- Timeline math ----------------------------------------------------------

def compute_scene_start_times(
    durations: list[float], xfade_duration: float
) -> list[float]:
    """For N clips with `xfade_duration` overlap between every adjacent
    pair, compute when each scene visibly starts in the OUTPUT timeline.

    Scene 0 starts at 0. Scene i starts when scene (i-1)'s xfade out
    completes — which is `cumulative_durations[0..i-1] - i * xfade_duration`.
    """
    starts = [0.0]
    cum = 0.0
    for i in range(len(durations) - 1):
        cum += durations[i]
        starts.append(cum - (i + 1) * xfade_duration)
    return starts


def _fmt_srt_time(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def manifest_to_srt(
    clips: list[dict[str, Any]],
    durations: list[float],
    xfade_duration: float,
    *,
    fade_in: float = 0.4,
    fade_tail: float = 0.5,
) -> str:
    """Build SRT text for an episode from the per-scene clip entries.

    Each clip's `line` field becomes one caption block, timed across the
    scene's visible range minus a small fade-in (so the caption doesn't
    appear at the exact instant of the cut) and a fade-tail (so it
    clears before the next cut)."""
    starts = compute_scene_start_times(durations, xfade_duration)
    out: list[str] = []
    counter = 1
    for i, c in enumerate(clips):
        line = (c.get("line") or "").strip()
        if not line:
            continue
        start = starts[i] + fade_in
        end = starts[i] + max(durations[i] - fade_tail, fade_in + 0.5)
        if end <= start:
            continue
        out.append(str(counter))
        out.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
        out.append(line)
        out.append("")
        counter += 1
    return "\n".join(out)


# -- Graphics package -------------------------------------------------------

@dataclass
class GraphicsPackage:
    """Resolved asset paths + per-scene timing for one episode's graphics
    pass. All fields optional — None means that layer is skipped."""

    brand_bug: str | None = None              # PNG path
    segment_title: str | None = None          # PNG path
    segment_title_window: tuple[float, float] = (2.0, 5.0)  # (start, end) seconds

    # Per-speaker ID → PNG path. Entries that aren't featured in any scene
    # don't need to be present.
    lower_thirds: dict[str, str] = field(default_factory=dict)
    # Parallel arrays the same length as `clips`. scene_speaker_ids[i] is
    # the featured speaker for scene i (or "" / None for ensemble shots).
    scene_speaker_ids: list[str] = field(default_factory=list)
    scene_starts: list[float] = field(default_factory=list)
    scene_durations: list[float] = field(default_factory=list)
    lower_third_offset: float = 0.5    # appear 0.5s after scene start
    lower_third_hold: float = 3.5      # then hold 3.5s

    # SRT path for caption burn-in. Always None on this build — Homebrew
    # ffmpeg ships without libass and the `subtitles` filter doesn't
    # exist. Field is kept on the dataclass so call sites that read it
    # don't break, and so a future PNG-overlay caption implementation
    # has a slot to populate without changing the protocol.
    captions_srt: str | None = None


def build_graphics_package(
    *,
    manifest: dict[str, Any],
    clip_paths: list[str],
    repo_root: str,
    work_dir: str,
    xfade_duration: float = 0.1,
    enable_brand_bug: bool = False,
    enable_segment_title: bool = False,
    enable_lower_thirds: bool = True,
    enable_captions: bool = False,
) -> GraphicsPackage:
    """Resolve every graphics asset for one segment.

    For each layer, prefer a designer-supplied PNG under
    `<repo_root>/assets/graphics/...` if it exists. Otherwise generate a
    fallback PNG with Pillow into `<work_dir>/.graphics/...`.

    Captions (SRT) are always generated from manifest data — there's no
    designer asset alternative.
    """
    pkg = GraphicsPackage()

    clips = manifest.get("clips") or []
    panel = manifest.get("panel") or []
    panel_by_id = {p["id"]: p for p in panel if p.get("id")}
    headline = ((manifest.get("story") or {}).get("headline") or "").strip()

    # Filter clips to only those that match a ready scene file (in clip_paths
    # order). The clip_paths ordering matches the editor's pass-1 input,
    # which is what the timeline math uses.
    ready_clips: list[dict[str, Any]] = []
    seg_dir = os.path.dirname(clip_paths[0]) if clip_paths else None
    for c in clips:
        if c.get("status") != "ready":
            continue
        ready_clips.append(c)

    # Ensure timeline math agrees with what's actually in the output: probe
    # each ready clip's duration so caption start times match the blended
    # output exactly.
    durations = [_probe_duration(p) for p in clip_paths]
    if any(d <= 0 for d in durations):
        # Fall back to the manifest's `duration` field on the matching
        # script scene if probe failed (e.g. corrupt mp4).
        log.warning("probe failed for at least one clip; using script durations")
        durations = [
            float(c.get("duration") or 10.0) for c in ready_clips[: len(clip_paths)]
        ]

    pkg.scene_starts = compute_scene_start_times(durations, xfade_duration)
    pkg.scene_durations = durations
    pkg.scene_speaker_ids = [
        c.get("speaker_id") or _infer_speaker_id(c, panel_by_id)
        for c in ready_clips[: len(clip_paths)]
    ]

    cache_root = os.path.join(work_dir, ".graphics")
    os.makedirs(cache_root, exist_ok=True)

    # ---- Brand bug ----
    if enable_brand_bug:
        designer = os.path.join(repo_root, "assets", "graphics", "brand_bug.png")
        if os.path.exists(designer):
            pkg.brand_bug = designer
        else:
            target = os.path.join(cache_root, "brand_bug.png")
            make_brand_bug_png(target)
            pkg.brand_bug = target

    # ---- Segment title ----
    if enable_segment_title and headline:
        designer = os.path.join(
            repo_root, "assets", "graphics", "segment_titles", "current.png"
        )
        if os.path.exists(designer):
            pkg.segment_title = designer
        else:
            target = os.path.join(cache_root, "segment_title.png")
            make_segment_title_png(target, headline=headline)
            pkg.segment_title = target

    # ---- Lower thirds (just the name) ----
    if enable_lower_thirds:
        unique_speakers = {sid for sid in pkg.scene_speaker_ids if sid}
        for sid in unique_speakers:
            persona = panel_by_id.get(sid) or {}
            name = persona.get("name") or sid.replace("_", " ").title()

            designer = os.path.join(
                repo_root, "assets", "graphics", "lower_thirds", f"{sid}.png"
            )
            if os.path.exists(designer):
                pkg.lower_thirds[sid] = designer
            else:
                target = os.path.join(cache_root, "lower_thirds", f"{sid}.png")
                make_lower_third_png(target, name=name)
                pkg.lower_thirds[sid] = target

    # Captions (SRT burn-in) intentionally not generated here — Homebrew
    # ffmpeg ships without libass, the `subtitles` filter is unavailable.
    # If you reinstall ffmpeg with libass, restore the captions block:
    #   if enable_captions:
    #       srt = manifest_to_srt(ready_clips[:len(clip_paths)], durations, xfade)
    #       ...write srt + populate pkg.captions_srt

    return pkg


def _infer_speaker_id(
    clip: dict[str, Any], panel_by_id: dict[str, dict[str, Any]]
) -> str:
    """Best-effort: match the clip's speaker name to a panel id."""
    name = (clip.get("speaker") or "").strip()
    if not name:
        return ""
    for pid, p in panel_by_id.items():
        if p.get("name", "").strip().lower() == name.lower():
            return pid
    return ""


def _probe_duration(path: str) -> float:
    """Container duration via ffprobe. 0.0 on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


# -- Pass 3: graphics overlay ffmpeg ---------------------------------------

def apply_graphics_pass(
    *,
    video_path: str,
    output_path: str,
    pkg: GraphicsPackage,
    work_dir: str | None = None,
    bug_position: str = "top-right",
    bug_top_offset: int = 28,
    bug_side_offset: int = 24,
    lower_third_y_from_bottom: int = 200,
    lower_third_x: int = 40,
    captions_margin_v: int = 70,
    crf: int = 18,
    preset: str = "medium",
) -> dict[str, Any]:
    """Composite the graphics package onto the assembled episode.

    Two ffmpeg invocations (Strategy B from the spec — one pass per
    type of layer; cleaner quoting since subtitles in `filter_complex`
    chokes on the comma-rich `force_style` string):

      Pass 3a — overlays (brand bug, segment title, lower thirds)
                via `-filter_complex` with stacked overlay filters.
      Pass 3b — captions burn-in via `-vf subtitles=...` with simpler
                shell quoting that survives commas in force_style.

    If only one layer-class has assets, only that pass runs. If neither,
    the input is just rewrapped to `output_path`.
    """
    layers: list[str] = []
    have_overlays = bool(pkg.brand_bug or pkg.segment_title or pkg.lower_thirds)
    have_captions = bool(pkg.captions_srt)

    if not have_overlays and not have_captions:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path, "-c", "copy",
            "-movflags", "+faststart", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"copy failed: {result.stderr[-600:]}")
        return {
            "output_path": output_path,
            "size_bytes": os.path.getsize(output_path),
            "graphics": "none",
        }

    # Pass 3a: overlays. Skip if no overlay assets — captions input is
    # just `video_path` directly.
    overlays_out = video_path
    if have_overlays:
        overlays_out = (
            os.path.join(work_dir, "with_overlays.mp4")
            if work_dir else output_path
        )
        if work_dir is None and have_captions:
            # We need an intermediate; can't both overlay-out and
            # caption-out to the same final path.
            overlays_out = output_path + ".overlays.mp4"
        _run_overlays_pass(
            video_path=video_path,
            output_path=overlays_out,
            pkg=pkg,
            bug_position=bug_position,
            bug_top_offset=bug_top_offset,
            bug_side_offset=bug_side_offset,
            lower_third_y_from_bottom=lower_third_y_from_bottom,
            lower_third_x=lower_third_x,
            crf=crf,
            preset=preset,
        )
        if pkg.brand_bug:
            layers.append("brand_bug")
        if pkg.segment_title:
            layers.append("segment_title")
        if pkg.lower_thirds:
            lt_count = sum(
                1 for s in pkg.scene_speaker_ids if s in pkg.lower_thirds
            )
            layers.append(f"lower_thirds×{lt_count}")

    # Pass 3b: captions burn-in via -vf (simpler quoting than filter_complex).
    if have_captions:
        _run_captions_pass(
            video_path=overlays_out,
            output_path=output_path,
            srt_path=pkg.captions_srt,  # type: ignore[arg-type]
            margin_v=captions_margin_v,
            crf=crf,
            preset=preset,
        )
        layers.append("captions")
        # Clean up the overlays intermediate if we created it.
        if overlays_out != video_path and overlays_out != output_path:
            try:
                os.remove(overlays_out)
            except OSError:
                pass
    elif have_overlays and overlays_out != output_path:
        # No captions pass; ensure overlays output is at the requested path.
        os.replace(overlays_out, output_path)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("apply_graphics_pass produced empty output")

    return {
        "output_path": output_path,
        "size_bytes": os.path.getsize(output_path),
        "graphics": ", ".join(layers) or "none",
    }


def _run_overlays_pass(
    *,
    video_path: str,
    output_path: str,
    pkg: GraphicsPackage,
    bug_position: str,
    bug_top_offset: int,
    bug_side_offset: int,
    lower_third_y_from_bottom: int,
    lower_third_x: int,
    crf: int,
    preset: str,
) -> None:
    """Pass 3a — composite brand bug + segment title + lower thirds via
    -filter_complex. Audio is stream-copied (-c:a copy)."""
    inputs: list[str] = [video_path]
    asset_indices: dict[str, int] = {}

    def _add_input(path: str) -> int:
        if path in asset_indices:
            return asset_indices[path]
        idx = len(inputs)
        inputs.append(path)
        asset_indices[path] = idx
        return idx

    bug_idx = _add_input(pkg.brand_bug) if pkg.brand_bug else None
    title_idx = _add_input(pkg.segment_title) if pkg.segment_title else None
    lt_idx_by_speaker: dict[str, int] = {
        sid: _add_input(path) for sid, path in pkg.lower_thirds.items()
    }

    chains: list[str] = []
    last = "[0:v]"
    counter = 0

    def _next_label() -> str:
        nonlocal counter
        counter += 1
        return f"[v{counter}]"

    # ---- Brand bug (always-on after t=1) ----
    if bug_idx is not None:
        if bug_position == "top-right":
            pos = f"W-w-{bug_side_offset}:{bug_top_offset}"
        elif bug_position == "top-left":
            pos = f"{bug_side_offset}:{bug_top_offset}"
        elif bug_position == "bottom-right":
            pos = f"W-w-{bug_side_offset}:H-h-{bug_top_offset}"
        else:
            pos = f"{bug_side_offset}:H-h-{bug_top_offset}"
        nxt = _next_label()
        chains.append(
            f"{last}[{bug_idx}:v]overlay={pos}:enable='gte(t,1)':format=auto{nxt}"
        )
        last = nxt

    # ---- Segment title (one-shot with alpha fade) ----
    if title_idx is not None:
        t0, t1 = pkg.segment_title_window
        fade_dur = min(0.4, (t1 - t0) / 4)
        chains.append(
            f"[{title_idx}:v]format=yuva420p,"
            f"fade=in:st=0:d={fade_dur:.3f}:alpha=1,"
            f"fade=out:st={(t1 - t0 - fade_dur):.3f}:d={fade_dur:.3f}:alpha=1"
            f"[title_faded]"
        )
        nxt = _next_label()
        chains.append(
            f"{last}[title_faded]overlay=0:(H-h)/2:"
            f"enable='between(t,{t0:.3f},{t1:.3f})':format=auto{nxt}"
        )
        last = nxt

    # ---- Lower thirds (one per scene) ----
    if lt_idx_by_speaker and pkg.scene_speaker_ids:
        for i, sid in enumerate(pkg.scene_speaker_ids):
            if not sid or sid not in lt_idx_by_speaker:
                continue
            scene_start = pkg.scene_starts[i]
            scene_dur = pkg.scene_durations[i]
            lt_start = scene_start + pkg.lower_third_offset
            lt_end = min(
                lt_start + pkg.lower_third_hold,
                scene_start + scene_dur - 0.2,
            )
            if lt_end <= lt_start:
                continue
            lt_input = lt_idx_by_speaker[sid]
            nxt = _next_label()
            chains.append(
                f"{last}[{lt_input}:v]overlay={lower_third_x}:"
                f"H-h-{lower_third_y_from_bottom}:"
                f"enable='between(t,{lt_start:.3f},{lt_end:.3f})':format=auto{nxt}"
            )
            last = nxt

    # Re-label the final filter output as [vout] for -map.
    if chains:
        chains[-1] = chains[-1][: chains[-1].rfind(last)] + "[vout]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in inputs:
        cmd.extend(["-i", p])
    cmd.extend([
        "-filter_complex", ";".join(chains),
        "-map", "[vout]",
        "-map", "0:a",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"overlays pass ffmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr[-1500:]}"
        )


def _run_captions_pass(
    *,
    video_path: str,
    output_path: str,
    srt_path: str,
    margin_v: int,
    crf: int,
    preset: str,
) -> None:
    """Pass 3b — burn captions via `-vf subtitles=...`.

    ffmpeg's filter parser doesn't honor single quotes for option-value
    grouping the way the spec implied; commas inside `force_style` get
    parsed as filter separators. The fix is to backslash-escape every
    comma in force_style so they're treated as literal characters in
    the value rather than as filter delimiters."""
    # ASS force_style values use `&H` hex BGR. White text, semi-transparent
    # black box, bottom-center. Commas escaped with `\,` so they don't
    # terminate the option value.
    style_pairs = [
        "FontName=Helvetica",
        "FontSize=18",
        "PrimaryColour=&H00FFFFFF",
        "OutlineColour=&H80000000",
        "BorderStyle=3",
        "Outline=4",
        "Shadow=0",
        f"MarginV={margin_v}",
        "Alignment=2",
    ]
    # Join with literal-escaped commas: \, in the filter string.
    force_style = "\\,".join(style_pairs)
    vf = f"subtitles={srt_path}:force_style={force_style}"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"captions pass ffmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr[-1500:]}"
        )
