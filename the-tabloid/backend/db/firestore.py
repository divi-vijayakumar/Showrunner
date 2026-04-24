"""Thin Firestore client. Falls back to an in-memory stub when mock mode is on.

The real client uses firebase_admin so it can upload video files to Storage too.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from ..config import settings

log = logging.getLogger(__name__)


class _MockStore:
    """Process-local stand-in for Firestore. Good enough for dev + demos."""

    def __init__(self) -> None:
        self.segments: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def new_id(self) -> str:
        return f"seg_{uuid.uuid4().hex[:10]}"


_MOCK = _MockStore()


class FirestoreClient:
    """Async façade. Writes go through the real firestore client when configured,
    otherwise they land in the process-local `_MOCK` store."""

    def __init__(self) -> None:
        self._real = None
        self._bucket = None
        if not settings().mock:
            self._init_real()

    def _init_real(self) -> None:
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, storage

            if not firebase_admin._apps:
                cred_path = settings().firebase_credentials
                if cred_path and os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                else:
                    cred = credentials.ApplicationDefault()
                firebase_admin.initialize_app(
                    cred,
                    {
                        "projectId": settings().firebase_project_id or None,
                        "storageBucket": settings().firebase_storage_bucket or None,
                    },
                )
            self._real = firestore.client()
            if settings().firebase_storage_bucket:
                self._bucket = storage.bucket()
        except Exception as exc:
            log.warning("Firestore init failed (%s) — falling back to in-memory mock", exc)
            self._real = None

    # -- Segment doc -------------------------------------------------------

    async def create_segment(self, channel: str) -> str:
        seg_id = _MOCK.new_id()
        doc = {
            "channel": channel,
            "status": "queued",
            "progress": 0,
            "created_at": time.time(),
        }
        if self._real:
            await asyncio.to_thread(
                self._real.collection("segments").document(seg_id).set, doc
            )
        else:
            _MOCK.segments[seg_id] = doc
            _MOCK.messages[seg_id] = []
        return seg_id

    async def get_segment(self, segment_id: str) -> dict[str, Any] | None:
        if self._real:
            snap = await asyncio.to_thread(
                self._real.collection("segments").document(segment_id).get
            )
            return snap.to_dict() if snap.exists else None
        return _MOCK.segments.get(segment_id)

    async def update_segment(self, segment_id: str, patch: dict[str, Any]) -> None:
        if self._real:
            doc_ref = self._real.collection("segments").document(segment_id)
            await asyncio.to_thread(doc_ref.set, patch, True)  # merge=True
        else:
            seg = _MOCK.segments.setdefault(segment_id, {})
            seg.update(patch)

    async def write_message(self, segment_id: str, message: dict[str, Any]) -> None:
        payload = {**message, "created_at": time.time()}
        if self._real:
            await asyncio.to_thread(
                self._real.collection("segments")
                .document(segment_id)
                .collection("messages")
                .add,
                payload,
            )
        else:
            _MOCK.messages.setdefault(segment_id, []).append(payload)

    async def list_messages(self, segment_id: str) -> list[dict[str, Any]]:
        if self._real:
            col = (
                self._real.collection("segments")
                .document(segment_id)
                .collection("messages")
                .order_by("seq")
            )
            docs = await asyncio.to_thread(lambda: list(col.stream()))
            return [d.to_dict() for d in docs]
        return list(_MOCK.messages.get(segment_id, []))

    # -- Storage -----------------------------------------------------------

    async def upload_video(self, local_path: str, segment_id: str) -> str:
        """Upload final segment video and return a public URL."""
        if not self._real or not self._bucket:
            # Mock mode: return the local path (frontend is probably also mocking)
            return f"file://{local_path}"

        blob_name = f"segments/{segment_id}.mp4"
        blob = self._bucket.blob(blob_name)

        def _upload() -> str:
            blob.upload_from_filename(local_path, content_type="video/mp4")
            blob.make_public()
            return blob.public_url

        return await asyncio.to_thread(_upload)
