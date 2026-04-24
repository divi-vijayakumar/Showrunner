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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import CHANNELS, channel_or_raise, settings
from .db.firestore import FirestoreClient
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
async def generate(channel: str, body: GenerateRequest | None = None) -> GenerateResponse:
    try:
        channel_or_raise(channel)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    db = FirestoreClient()
    segment_id = await db.create_segment(channel)

    personas_override = body.personas if body else None

    # Hand off to Celery. In mock mode without a running worker, this will still
    # enqueue the task; for pure-local dev without Redis, call the async form
    # directly by setting CELERY_TASK_ALWAYS_EAGER=1 on the worker.
    celery_generate.delay(segment_id, channel, personas_override)

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
