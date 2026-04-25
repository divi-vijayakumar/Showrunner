"""Thin Firestore client. Falls back to an in-memory stub when mock mode is on.

The real client uses firebase_admin so it can upload video files to Storage too.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from typing import Any

from ..config import settings


# Where finished media lives when Firebase Storage isn't configured.
# Defaults to <project>/data/{videos,audio} (gitignored), survives restarts
# so users can re-watch / re-listen anything ever generated.
def _data_subdir(name: str) -> str:
    base = os.path.abspath(os.path.expanduser(settings().tabloid_data_dir))
    return os.path.join(base, name)


LOCAL_VIDEO_DIR = _data_subdir("videos")
LOCAL_AUDIO_DIR = _data_subdir("audio")

log = logging.getLogger(__name__)


class _MockStore:
    """Process-local stand-in for Firestore. Good enough for dev + demos."""

    def __init__(self) -> None:
        self.segments: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def new_id(self) -> str:
        return f"seg_{uuid.uuid4().hex[:10]}"


_MOCK = _MockStore()


# Module-level cache so we don't re-run firebase init (and re-spam warnings)
# for every request.
_REAL_DB: Any = None
_REAL_BUCKET: Any = None
_INIT_TRIED: bool = False


def _init_firebase_once() -> tuple[Any, Any]:
    """Initialise firebase-admin at most once per process. Returns (db, bucket)
    tuple; either side may be None if not configured."""
    global _REAL_DB, _REAL_BUCKET, _INIT_TRIED
    if _INIT_TRIED:
        return _REAL_DB, _REAL_BUCKET
    _INIT_TRIED = True

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore, storage

        cred_path = settings().firebase_credentials
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            # gcloud Application Default Credentials. But ADC itself respects
            # GOOGLE_APPLICATION_CREDENTIALS — if that env var points at a
            # non-existent file (very common when users paste `.env.example`
            # defaults), ADC errors out instead of falling through to the
            # gcloud user creds at ~/.config/gcloud/application_default_credentials.json.
            # Clear it so ADC uses the gcloud login.
            if cred_path and not os.path.exists(cred_path):
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            cred = credentials.ApplicationDefault()

        init_options: dict[str, str] = {}
        if settings().firebase_project_id:
            init_options["projectId"] = settings().firebase_project_id
        if settings().firebase_storage_bucket:
            init_options["storageBucket"] = settings().firebase_storage_bucket

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, init_options or None)

        _REAL_DB = firestore.client()
        if settings().firebase_storage_bucket:
            _REAL_BUCKET = storage.bucket()

    except Exception as exc:
        log.warning("Firestore init failed (%s) — falling back to in-memory mock", exc)
        _REAL_DB = None
        _REAL_BUCKET = None

    return _REAL_DB, _REAL_BUCKET


class FirestoreClient:
    """Async façade. Writes go through the real firestore client when configured,
    otherwise they land in the process-local `_MOCK` store."""

    def __init__(self) -> None:
        self._real = None
        self._bucket = None
        if not settings().mock:
            self._real, self._bucket = _init_firebase_once()

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

    async def list_segments(
        self,
        limit: int = 20,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent segments, newest first. Used by the History page."""
        if self._real:
            from firebase_admin import firestore as _fs
            col = self._real.collection("segments")
            if channel:
                col = col.where("channel", "==", channel)
            col = col.order_by("created_at", direction=_fs.Query.DESCENDING).limit(limit)

            def _stream() -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for d in col.stream():
                    data = d.to_dict() or {}
                    data["id"] = d.id
                    out.append(data)
                return out

            return await asyncio.to_thread(_stream)

        # Mock fallback — sort the in-process dict by created_at desc.
        items = []
        for sid, data in _MOCK.segments.items():
            row = {**data, "id": sid}
            if not channel or row.get("channel") == channel:
                items.append(row)
        items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return items[:limit]

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
        """Publish the finished segment video and return a URL the browser
        can actually fetch.

        Three modes:
        - Firebase Storage configured → upload there, return public_url.
        - No bucket but Firestore is configured → copy into LOCAL_VIDEO_DIR
          so /api/videos/{id}.mp4 can serve it; return an absolute URL built
          from PUBLIC_BASE_URL.
        - Full mock (no real Firestore either) → same as above; frontend will
          hit the backend endpoint.
        """
        if self._bucket:
            blob_name = f"segments/{segment_id}.mp4"
            blob = self._bucket.blob(blob_name)

            def _upload() -> str:
                blob.upload_from_filename(local_path, content_type="video/mp4")
                blob.make_public()
                return blob.public_url

            return await asyncio.to_thread(_upload)

        # Storage-less path: copy into a stable location the backend serves.
        os.makedirs(LOCAL_VIDEO_DIR, exist_ok=True)
        dest = os.path.join(LOCAL_VIDEO_DIR, f"{segment_id}.mp4")
        await asyncio.to_thread(shutil.copyfile, local_path, dest)
        base = settings().public_base_url.rstrip("/")
        return f"{base}/api/videos/{segment_id}.mp4"

    async def upload_audio(self, local_path: str, segment_id: str) -> str:
        """Publish a podcast mp3. Same three-mode fallback as upload_video."""
        if self._bucket:
            blob_name = f"segments/{segment_id}.mp3"
            blob = self._bucket.blob(blob_name)

            def _upload() -> str:
                blob.upload_from_filename(local_path, content_type="audio/mpeg")
                blob.make_public()
                return blob.public_url

            return await asyncio.to_thread(_upload)

        os.makedirs(LOCAL_AUDIO_DIR, exist_ok=True)
        dest = os.path.join(LOCAL_AUDIO_DIR, f"{segment_id}.mp3")
        await asyncio.to_thread(shutil.copyfile, local_path, dest)
        base = settings().public_base_url.rstrip("/")
        return f"{base}/api/audio/{segment_id}.mp3"
