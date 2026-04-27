"""FastAPI entry point for The Tabloid backend.

Run:
  uvicorn backend.main:app --reload --port 8000

The only real action is POST /api/generate/{channel} which creates the segment
doc and hands off to a Celery worker. The frontend subscribes to Firestore for
everything after that.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncio
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .agents.story_selector import enrich_with_bodies, fetch_headlines
from .config import CHANNELS, channel_or_raise, settings
from .db.firestore import LOCAL_AUDIO_DIR, LOCAL_IMAGE_DIR, LOCAL_VIDEO_DIR, FirestoreClient
from .jobs.pipeline import _generate as run_pipeline_async
from .jobs.pipeline import _generate_direct as run_direct_async
from .jobs.pipeline import generate_segment as celery_generate
from .jobs.pipeline import (
    SEGMENTS_DIR as DIRECT_SEGMENTS_DIR,
    load_segment_manifest as load_direct_manifest,
)
from .personas import PERSONAS, default_panel

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="The Tabloid")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateRequest(BaseModel):
    personas: list[dict[str, Any]] | None = None  # optional user-swapped panel
    mode: str | None = None  # "tabloid" (default) | "podcast"
    # Optional: lock the segment to this exact RSS story instead of letting
    # the agent pick one. The frontend's StoryPicker provides this.
    story: dict[str, Any] | None = None


class GenerateResponse(BaseModel):
    segment_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mock": "on" if settings().mock else "off"}


def _load_pinned_stories(channel: str) -> list[dict[str, Any]]:
    """Read pinned/manually-added stories for a channel from a JSON file
    at $TABLOID_DATA_DIR/pinned_stories.json. Schema:

      { "india_politics": [ {"title": "...", "link": "...", "source": "...",
                             "summary": "...", "body": "..."}, ... ], ... }

    Useful for re-running stories that have rolled off the live RSS feed.
    Pinned entries are prepended to the list returned to the StoryPicker."""
    import json
    from .db.firestore import _data_subdir  # reuses the data dir resolver

    path = os.path.join(os.path.dirname(_data_subdir("videos")), "pinned_stories.json")
    if not os.path.exists(path):
        return []
    try:
        data = json.loads(open(path).read())
    except Exception as exc:
        log.warning("pinned_stories.json parse failed: %s", exc)
        return []
    items = data.get(channel) or []
    return [i for i in items if i.get("title")]


@app.get("/api/channels/{channel}/stories")
async def channel_stories(channel: str, limit: int = 8) -> dict[str, Any]:
    """Return enriched candidate stories for the StoryPicker UI.

    Pulls RSS, fetches article bodies for the top `limit` headlines, returns
    them as-is — no LLM selection. The user picks one or hits "Auto-pick" to
    let the agent decide downstream.

    Pinned stories from data/pinned_stories.json are prepended for re-running
    items that have rolled off the live RSS feed.
    """
    try:
        channel_or_raise(channel)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    headlines = await fetch_headlines(channel)
    enriched = await enrich_with_bodies(headlines, limit=limit)

    candidates: list[dict[str, Any]] = []

    # Pinned first — these are explicit user picks, surfaced at the top of
    # the StoryPicker. They carry "pinned": true so the UI can badge them.
    for h in _load_pinned_stories(channel):
        body = (h.get("body") or h.get("summary") or "").strip()
        candidates.append(
            {
                "title": h["title"],
                "source": h.get("source", ""),
                "link": h.get("link", ""),
                "summary": (h.get("summary") or "").strip()[:300],
                "body_preview": body[:500],
                "has_body": bool(h.get("body")),
                "pinned": True,
            }
        )

    # Then live RSS, trimmed to fit the requested limit overall.
    for h in enriched[:max(0, limit - len(candidates))]:
        if not h.get("title"):
            continue
        body = (h.get("body") or h.get("summary") or "").strip()
        candidates.append(
            {
                "title": h["title"],
                "source": h.get("source", ""),
                "link": h.get("link", ""),
                "summary": (h.get("summary") or "").strip()[:300],
                "body_preview": body[:500],
                "has_body": bool(h.get("body")),
            }
        )
    return {"channel": channel, "stories": candidates}


@app.get("/api/channels")
async def list_channels() -> dict[str, Any]:
    """Return channel metadata + default panels for the frontend."""
    out: dict[str, Any] = {}
    for cid, cfg in CHANNELS.items():
        out[cid] = {
            "id": cid,
            "label": cfg["label"],
            "icon": cfg["icon"],
            "color": cfg["color"],
            "default_panel": default_panel(cid),
            "alternatives": PERSONAS.get(cid, {}).get("alternatives", []),
        }
    return out


@app.post("/api/generate/{channel}", response_model=GenerateResponse)
async def generate(
    channel: str,
    background_tasks: BackgroundTasks,
    body: GenerateRequest | None = None,
) -> GenerateResponse:
    try:
        channel_or_raise(channel)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    mode = (body.mode if body and body.mode else "tabloid").lower()
    if mode not in ("tabloid", "podcast", "sample"):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    db = FirestoreClient()
    segment_id = await db.create_segment(channel)
    await db.update_segment(segment_id, {"mode": mode})
    personas_override = body.personas if body else None
    picked_story = body.story if body else None

    # Run inline as a FastAPI background task unless TABLOID_USE_CELERY=1.
    use_celery = os.getenv("TABLOID_USE_CELERY", "").lower() in ("1", "true", "yes")
    if use_celery and not settings().mock:
        celery_generate.delay(segment_id, channel, personas_override, mode, picked_story)
    else:
        background_tasks.add_task(
            lambda: asyncio.run(
                run_pipeline_async(segment_id, channel, personas_override, mode, picked_story)
            )
        )

    return GenerateResponse(segment_id=segment_id)


@app.get("/api/segments")
async def list_segments(
    limit: int = 20,
    channel: str | None = None,
) -> dict[str, Any]:
    """Recent episodes for the History page. Newest first."""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1..100")
    if channel:
        try:
            channel_or_raise(channel)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    db = FirestoreClient()
    rows = await db.list_segments(limit=limit, channel=channel)
    return {"segments": rows}


@app.get("/api/segment/{segment_id}")
async def get_segment(segment_id: str) -> dict[str, Any]:
    """Polling fallback if the frontend can't subscribe to Firestore."""
    db = FirestoreClient()
    seg = await db.get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="segment not found")
    messages = await db.list_messages(segment_id)
    return {"segment": seg, "messages": messages}


# -- Direct mode --------------------------------------------------------------

def _load_direct_script(script_name: str) -> dict[str, Any]:
    """Read a hand-authored 24-scene script from data/scripts/{name}.json."""
    import json
    from .db.firestore import _data_subdir

    if not script_name or any(c in script_name for c in "/\\."):
        raise HTTPException(status_code=400, detail="bad script name")
    path = os.path.join(
        os.path.dirname(_data_subdir("videos")), "scripts", f"{script_name}.json"
    )
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"script not found: {script_name}")
    try:
        return json.loads(open(path).read())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"script parse error: {exc}")


class DirectRequest(BaseModel):
    """Optional batching params for /api/generate-direct."""
    start: int = 1
    end: int | None = None
    segment_id: str | None = None  # if set, append to existing run


@app.post("/api/generate-direct/{script_name}", response_model=GenerateResponse)
async def generate_direct(
    script_name: str,
    background_tasks: BackgroundTasks,
    body: DirectRequest | None = None,
) -> GenerateResponse:
    """Bypasses RSS picker, debate engine, and script compiler. Reads a
    hand-authored script JSON from data/scripts/{name}.json and runs a
    slice of the Seedance scenes. Stores per-scene clip URLs (no concat)
    and writes a durable manifest at data/segments/{sid}/manifest.json.

    Examples:
      # Fresh run, scenes 1-5 only
      POST /api/generate-direct/skyroot  body={"start":1,"end":5}

      # Continue the same segment with scenes 6-10
      POST /api/generate-direct/skyroot
        body={"start":6,"end":10,"segment_id":"seg_xxxxx"}

      # Default: full run (scenes 1-end)
      POST /api/generate-direct/skyroot
    """
    body = body or DirectRequest()
    script_data = _load_direct_script(script_name)
    channel = script_data.get("channel", "india_politics")

    db = FirestoreClient()
    if body.segment_id:
        segment_id = body.segment_id
        # Re-hydrate the in-memory segment doc from manifest if uvicorn was
        # restarted between batches (mock store loses everything on restart).
        seg = await db.get_segment(segment_id)
        if not seg:
            manifest = load_direct_manifest(segment_id)
            if not manifest:
                raise HTTPException(
                    status_code=404,
                    detail=f"segment {segment_id} not found and no manifest on disk",
                )
            await db.update_segment(segment_id, {
                "channel": manifest.get("channel", channel),
                "mode": "direct",
                "story": manifest.get("story") or {},
                "panel": manifest.get("panel") or [],
                "clips": manifest.get("clips") or [],
                "stats": manifest.get("stats") or {},
                "status": "queued",
                "progress": 0,
            })
    else:
        segment_id = await db.create_segment(channel)

    background_tasks.add_task(
        lambda: asyncio.run(run_direct_async(
            segment_id, channel, script_data,
            start_scene=body.start, end_scene=body.end,
        ))
    )
    return GenerateResponse(segment_id=segment_id)


@app.get("/api/segment/{segment_id}/clips")
async def get_segment_clips(segment_id: str) -> dict[str, Any]:
    """Per-scene clip status for direct-mode segments. Reads from in-memory
    segment doc first; falls back to on-disk manifest so output survives
    uvicorn restarts (critical for hackathon preservation)."""
    db = FirestoreClient()
    seg = await db.get_segment(segment_id)
    if not seg:
        manifest = load_direct_manifest(segment_id)
        if not manifest:
            raise HTTPException(status_code=404, detail="segment not found")
        seg = {
            "status": "ready" if not (manifest.get("stats") or {}).get("queued") else "partial",
            "progress": 100,
            **manifest,
        }
    return {
        "segment_id": segment_id,
        "status": seg.get("status"),
        "progress": seg.get("progress"),
        "story": seg.get("story") or {},
        "clips": seg.get("clips") or [],
        "stats": seg.get("stats") or {},
    }


@app.post("/api/segments/{segment_id}/stitch")
async def stitch_segment_endpoint(segment_id: str) -> dict[str, Any]:
    """ffmpeg-concat all clips of a direct-mode segment into a single mp4
    with intro + outro music. Returns a dict with `video_url` (browser-
    playable) + duration / size / mode metadata.

    The intro/outro audio files are pulled from `data/audio/intro.mp3`
    and `outro.mp3` if present. The avatar PNG (from the script's
    `avatar_path`) is used as the visual under those stings, so the
    cold-open and sign-off feel on-brand."""
    from .sdk.providers.ffmpeg_direct import stitch_direct_segment
    from .jobs.pipeline import SEGMENTS_DIR, load_segment_manifest

    if not segment_id or any(c in segment_id for c in "/\\."):
        raise HTTPException(status_code=400, detail="bad segment id")

    manifest = load_segment_manifest(segment_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="segment not found")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Avatar: prefer the script's local avatar_path, fall back to the
    # uploaded Fal URL we cached. We use the LOCAL file for ffmpeg.
    avatar_path: str | None = None
    script_name = (manifest.get("story") or {}).get("script_name") or "skyroot"
    try:
        script_data = _load_direct_script(script_name)
        ap = script_data.get("avatar_path")
        if ap:
            full = ap if os.path.isabs(ap) else os.path.join(repo_root, ap)
            if os.path.exists(full):
                avatar_path = full
    except Exception:
        pass
    if avatar_path is None:
        # Default to the Skyroot avatar.
        candidate = os.path.join(repo_root, "scripts", "The_tabloid_set.png")
        if os.path.exists(candidate):
            avatar_path = candidate

    # Optional intro/outro music tracks.
    intro_audio = os.path.join(repo_root, "data", "audio", "intro.mp3")
    outro_audio = os.path.join(repo_root, "data", "audio", "outro.mp3")
    intro_audio = intro_audio if os.path.exists(intro_audio) else None
    outro_audio = outro_audio if os.path.exists(outro_audio) else None

    try:
        result = await asyncio.to_thread(
            stitch_direct_segment,
            segment_id,
            segments_dir=SEGMENTS_DIR,
            output_dir=LOCAL_VIDEO_DIR,
            intro_image_path=avatar_path,
            outro_image_path=avatar_path,
            intro_audio_path=intro_audio,
            outro_audio_path=outro_audio,
        )
    except Exception as exc:
        log.exception("stitch failed for %s", segment_id)
        raise HTTPException(status_code=500, detail=f"stitch failed: {exc}")

    base = settings().public_base_url.rstrip("/")
    return {
        **result,
        "video_url": f"{base}/api/videos/{segment_id}_full.mp4",
    }


@app.get("/api/segments/{segment_id}/scene/{scene_number}.mp4")
async def get_segment_scene(segment_id: str, scene_number: str):
    """Serve a locally-cached scene mp4 for a direct-mode segment. Stable
    URL so the player + manifest both reference it without depending on
    Fal CDN expiry."""
    if (
        not segment_id or any(c in segment_id for c in "/\\.")
        or not scene_number or any(c in scene_number for c in "/\\.")
    ):
        raise HTTPException(status_code=400, detail="bad path")
    path = os.path.join(DIRECT_SEGMENTS_DIR, segment_id, f"scene_{scene_number}.mp4")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="scene not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{segment_id}_scene_{scene_number}.mp4")


_DIRECT_PLAYER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>The Tabloid — Direct Player</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b0b0f; color: #e6e6ea; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  header { padding: 16px 24px; border-bottom: 1px solid #1a1a22; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 16px; font-weight: 600; margin: 0; letter-spacing: 0.04em; text-transform: uppercase; color: #fa3e3e; }
  header .meta { font-size: 13px; color: #8a8a96; }
  main { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; padding: 16px 24px; height: calc(100vh - 60px); }
  .stage { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .stage video { width: 100%; max-height: 50vh; background: #000; border-radius: 8px; outline: none; }
  .stage .section-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #8a8a96; margin: 8px 0 -2px 0; }
  .stage .final-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .stage .stitch-btn { background: #fa3e3e; color: #fff; border: 0; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; }
  .stage .stitch-btn:disabled { background: #2a2a36; color: #666; cursor: not-allowed; }
  .stage .stitch-btn:hover:not(:disabled) { background: #ff5252; }
  .stage .stitch-status { font-size: 12px; color: #8a8a96; }
  .stage .now { font-size: 14px; color: #c5c5cf; line-height: 1.4; }
  .stage .now .num { color: #fa3e3e; font-weight: 700; margin-right: 8px; }
  .stage .now .speaker { color: #ffd24a; font-weight: 600; margin-right: 8px; }
  .strip { overflow-y: auto; border-left: 1px solid #1a1a22; padding-left: 12px; }
  .strip h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: #8a8a96; margin: 4px 0 12px; }
  .tile { background: #15151c; border: 1px solid #232330; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s; font-size: 13px; line-height: 1.35; }
  .tile:hover { border-color: #3a3a4a; }
  .tile.active { border-color: #fa3e3e; background: #1a1015; }
  .tile .row1 { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
  .tile .num { font-weight: 700; color: #c5c5cf; font-size: 12px; }
  .tile .speaker { color: #ffd24a; font-weight: 600; font-size: 12px; }
  .tile .status { font-size: 11px; padding: 1px 6px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile .status.ready { background: #1f3a26; color: #6ee7a3; }
  .tile .status.failed { background: #3a1f1f; color: #ff8a8a; }
  .tile .status.pending { background: #2a2a36; color: #8a8a96; }
  .tile .status.queued { background: #1a1a22; color: #555560; }
  .tile .status.skipped { background: #2a1f10; color: #c89060; }
  .tile .line { color: #c5c5cf; font-size: 12px; font-style: italic; }
  .tile .err { color: #ff8a8a; font-size: 11px; margin-top: 4px; word-break: break-word; }
  .controls { display: flex; gap: 8px; align-items: center; }
  .controls button { background: #232330; color: #e6e6ea; border: 1px solid #3a3a4a; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .controls button:hover { border-color: #fa3e3e; }
  .progress { font-size: 12px; color: #8a8a96; }
</style>
</head>
<body>
<header>
  <h1>THE TABLOID — DIRECT</h1>
  <div class="meta" id="meta">loading…</div>
  <div class="controls">
    <button id="prev">◀ Prev</button>
    <button id="next">Next ▶</button>
    <button id="autoplay">Auto-play: ON</button>
    <span class="progress" id="progress"></span>
  </div>
</header>
<main>
  <div class="stage">
    <div class="section-label">Scene preview</div>
    <video id="player" controls playsinline></video>
    <div class="now" id="now">—</div>

    <div class="section-label">Final episode (with intro + outro music)</div>
    <video id="final-player" controls playsinline style="display:none"></video>
    <div class="final-row">
      <button id="stitch-btn" class="stitch-btn" disabled>Build final episode</button>
      <span class="stitch-status" id="stitch-status">Waiting for all scenes…</span>
    </div>
  </div>
  <div class="strip" id="strip">
    <h2>24 scenes</h2>
    <div id="tiles">loading…</div>
  </div>
</main>
<script>
const SEG_ID = "__SEGMENT_ID__";
const player = document.getElementById('player');
const tiles = document.getElementById('tiles');
const meta = document.getElementById('meta');
const now = document.getElementById('now');
const progress = document.getElementById('progress');
const autoBtn = document.getElementById('autoplay');
let clips = [];
let cur = 0;
let auto = true;

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderTiles() {
  tiles.innerHTML = clips.map((c, i) => `
    <div class="tile ${i === cur ? 'active' : ''}" data-i="${i}">
      <div class="row1">
        <span><span class="num">#${c.scene_number}</span> <span class="speaker">${escapeHtml(c.speaker)}</span></span>
        <span class="status ${c.status}">${c.status}${c.mode ? ' · ' + c.mode : ''}${c.cost_usd ? ' · $' + c.cost_usd.toFixed(2) : ''}</span>
      </div>
      <div class="line">"${escapeHtml(c.line)}"</div>
      ${c.error ? `<div class="err">${escapeHtml(c.error)}</div>` : ''}
    </div>
  `).join('');
  for (const tile of tiles.querySelectorAll('.tile')) {
    tile.addEventListener('click', () => { cur = parseInt(tile.dataset.i); load(); });
  }
}

function load() {
  const c = clips[cur];
  if (!c) return;
  now.innerHTML = `<span class="num">#${c.scene_number}</span><span class="speaker">${escapeHtml(c.speaker)}:</span> "${escapeHtml(c.line)}" — <em>${c.status}</em>`;
  if (c.video_url && c.status === 'ready') {
    player.src = c.video_url;
    player.play().catch(() => {});
  } else {
    player.removeAttribute('src');
    player.load();
  }
  renderTiles();
}

player.addEventListener('ended', () => {
  if (auto && cur < clips.length - 1) { cur++; load(); }
});

document.getElementById('prev').addEventListener('click', () => { if (cur > 0) { cur--; load(); }});
document.getElementById('next').addEventListener('click', () => { if (cur < clips.length - 1) { cur++; load(); }});
autoBtn.addEventListener('click', () => { auto = !auto; autoBtn.textContent = `Auto-play: ${auto ? 'ON' : 'OFF'}`; });

// Final-episode stitch UI state.
const stitchBtn = document.getElementById('stitch-btn');
const stitchStatus = document.getElementById('stitch-status');
const finalPlayer = document.getElementById('final-player');
let stitchInFlight = false;
let stitchedUrl = null;

function updateStitchButton(stats, clipsArr) {
  if (stitchedUrl) return; // already stitched, leave UI alone
  const allDone = clipsArr.length > 0 && clipsArr.every(c =>
    c.status === 'ready' || c.status === 'failed' || c.status === 'skipped'
  );
  const readyCount = clipsArr.filter(c => c.status === 'ready').length;
  if (stitchInFlight) {
    stitchBtn.disabled = true;
    stitchStatus.textContent = 'Stitching with intro + outro music…';
  } else if (allDone && readyCount > 0) {
    stitchBtn.disabled = false;
    stitchStatus.textContent = `Ready to stitch ${readyCount} scenes (+ intro & outro)`;
  } else {
    stitchBtn.disabled = true;
    stitchStatus.textContent = `Waiting for scenes (${readyCount}/${clipsArr.length} ready)`;
  }
}

stitchBtn.addEventListener('click', async () => {
  if (stitchInFlight || stitchedUrl) return;
  stitchInFlight = true;
  stitchBtn.disabled = true;
  stitchStatus.textContent = 'Stitching with intro + outro music…';
  try {
    const r = await fetch(`/api/segments/${SEG_ID}/stitch`, { method: 'POST' });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
    }
    const d = await r.json();
    stitchedUrl = d.video_url;
    finalPlayer.src = stitchedUrl;
    finalPlayer.style.display = 'block';
    finalPlayer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const dur = (d.duration_seconds || 0).toFixed(1);
    const sz = (d.size_mb || 0).toFixed(1);
    stitchStatus.innerHTML =
      `<a href="${stitchedUrl}" download style="color:#6ee7a3">⬇ download</a> · ` +
      `${dur}s, ${sz} MB · ${d.mode}`;
    stitchBtn.style.display = 'none';
  } catch (err) {
    stitchInFlight = false;
    stitchBtn.disabled = false;
    stitchStatus.textContent = `Stitch failed: ${err.message || err}`;
  }
});

async function poll() {
  try {
    const r = await fetch(`/api/segment/${SEG_ID}/clips`);
    const d = await r.json();
    clips = d.clips || [];
    const stats = d.stats || {};
    meta.textContent = `${d.story?.headline || ''} · ${d.status || '?'} · ${d.progress ?? 0}%`;
    // Compute live cost from clips (more accurate than stats during a run).
    const liveCost = clips.reduce((s, c) => s + (c.cost_usd || 0), 0);
    const costStr = `$${liveCost.toFixed(2)}`;
    progress.textContent = stats.total_scenes
      ? `${stats.succeeded}/${stats.total_scenes} ok, ${stats.failed} failed · ${costStr} spent`
      : `${clips.filter(c => c.status === 'ready').length}/${clips.length} ready · ${costStr} spent`;
    if (!player.src && clips[cur]?.video_url) load();
    renderTiles();
    updateStitchButton(stats, clips);
    if (d.status !== 'ready' && d.status !== 'failed' && !stitchedUrl) {
      setTimeout(poll, 4000);
    }
  } catch (e) {
    setTimeout(poll, 5000);
  }
}
poll();
</script>
</body>
</html>
"""


@app.get("/direct/{segment_id}", response_class=HTMLResponse)
async def direct_player(segment_id: str) -> HTMLResponse:
    """Self-contained vanilla-JS player for direct-mode segments. Polls
    /api/segment/{id}/clips, plays scenes sequentially with HTML5 video,
    shows debug strip with per-scene status."""
    if not segment_id or any(c in segment_id for c in "/\\."):
        raise HTTPException(status_code=400, detail="bad segment id")
    return HTMLResponse(_DIRECT_PLAYER_HTML.replace("__SEGMENT_ID__", segment_id))


@app.get("/api/audio/{segment_id}.mp3")
async def get_audio(segment_id: str):
    """Stream a finished podcast mp3 when Firebase Storage isn't configured."""
    if not segment_id or any(c in segment_id for c in "/\\."):
        raise HTTPException(status_code=400, detail="bad segment id")
    path = os.path.join(LOCAL_AUDIO_DIR, f"{segment_id}.mp3")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{segment_id}.mp3")


@app.get("/api/videos/{segment_id}.mp4")
async def get_video(segment_id: str):
    """Stream a finished segment's mp4 when Firebase Storage isn't configured.

    Accepts Range requests (FileResponse handles this), so the browser's video
    element can seek and resume. Only used when upload_video copied the file
    into LOCAL_VIDEO_DIR instead of Cloud Storage.
    """
    # Basic sanitisation: only accept the segment-id shape we hand out.
    if not segment_id or any(c in segment_id for c in "/\\."):
        raise HTTPException(status_code=400, detail="bad segment id")
    path = os.path.join(LOCAL_VIDEO_DIR, f"{segment_id}.mp4")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{segment_id}.mp4")


@app.get("/api/images/{filename}")
async def get_image(filename: str):
    """Serve extracted last-frame PNGs for the Seedance chaining feature.

    Seedance fetches `first_frame` server-side, so the URL must be reachable
    from BytePlus. In dev (PUBLIC_BASE_URL=localhost), this route exists but
    BytePlus can't fetch it — chaining silently degrades to MCU derivatives.
    """
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or not filename.endswith(".png")
    ):
        raise HTTPException(status_code=400, detail="bad filename")
    path = os.path.join(LOCAL_IMAGE_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, media_type="image/png", filename=filename)
