"""BytePlus Seed Speech (TTS) wrapper.

Returns a URL to the generated audio clip. In mock mode, writes a short
silent MP3 and returns a file:// URL.
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)


_MOCK_DIR = "/tmp/tabloid_mock_vo"


def _voice_id(voice_params: dict[str, Any]) -> str:
    """Map our persona voice knobs to a Seed Speech voice_id.

    Until we've confirmed the console's voice list, we use a simple scheme:
    {gender}_{warmth} with pace as a separate parameter. Swap for real IDs
    once they're provisioned.
    """
    gender = voice_params.get("gender", "female")
    warmth = voice_params.get("warmth", "medium")
    return f"{gender}_{warmth}"


async def generate_seed_speech(text: str, voice_params: dict[str, Any]) -> str:
    """Generate VO for a single line. Returns an audio URL."""
    if settings().mock:
        return _mock_silent_clip(text)

    payload = {
        "text": text,
        "voice_id": _voice_id(voice_params),
        "pace": voice_params.get("pace", 1.0),
        "format": "mp3",
    }
    headers = {
        "Authorization": f"Bearer {settings().seed_speech_api_key}",
        "Content-Type": "application/json",
    }
    base = settings().seed_speech_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{base}/tts", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # Seed Speech typically returns either {"audio_url": ...} or raw bytes.
        if "audio_url" in data:
            return data["audio_url"]
        raise RuntimeError(f"Unexpected Seed Speech response shape: {list(data)}")


def _mock_silent_clip(text: str) -> str:
    """Generate a short silent mp3 for offline dev. Duration ~= len(text)/15 seconds."""
    os.makedirs(_MOCK_DIR, exist_ok=True)
    path = os.path.join(_MOCK_DIR, f"vo_{uuid.uuid4().hex[:8]}.mp3")
    secs = max(1.5, min(6.0, len(text) / 15))
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=24000:cl=mono",
                "-t",
                f"{secs:.2f}",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # ffmpeg not present — touch the file so callers don't crash.
        open(path, "wb").close()
    return f"file://{path}"
