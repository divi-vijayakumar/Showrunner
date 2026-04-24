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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import CHANNELS, channel_or_raise, settings
from .db.firestore import LOCAL_AUDIO_DIR, LOCAL_VIDEO_DIR, FirestoreClient
from .jobs.pipeline import _generate as run_pipeline_async
from .jobs.pipeline import generate_segment as celery_generate
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


class GenerateResponse(BaseModel):
    segment_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mock": "on" if settings().mock else "off"}


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
    if mode not in ("tabloid", "podcast"):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    db = FirestoreClient()
    segment_id = await db.create_segment(channel)
    await db.update_segment(segment_id, {"mode": mode})
    personas_override = body.personas if body else None

    # Run inline as a FastAPI background task unless TABLOID_USE_CELERY=1.
    # Keeps local dev and demo-on-laptop zero-ops — no Redis/Celery worker needed.
    use_celery = os.getenv("TABLOID_USE_CELERY", "").lower() in ("1", "true", "yes")
    if use_celery and not settings().mock:
        celery_generate.delay(segment_id, channel, personas_override, mode)
    else:
        background_tasks.add_task(
            lambda: asyncio.run(run_pipeline_async(segment_id, channel, personas_override, mode))
        )

    return GenerateResponse(segment_id=segment_id)


@app.get("/api/segment/{segment_id}")
async def get_segment(segment_id: str) -> dict[str, Any]:
    """Polling fallback if the frontend can't subscribe to Firestore."""
    db = FirestoreClient()
    seg = await db.get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="segment not found")
    messages = await db.list_messages(segment_id)
    return {"segment": seg, "messages": messages}


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
