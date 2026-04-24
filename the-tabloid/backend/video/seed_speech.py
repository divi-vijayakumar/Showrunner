"""Text-to-speech wrapper.

Dispatches by TTS_PROVIDER env:
- byteplus (default): BytePlus Seed Speech (real hackathon path)
- elevenlabs:         ElevenLabs v2 multilingual — drop-in when Seed is unavailable

Returns either a URL the stitcher can fetch, or a `file://` path to audio we
wrote locally. The stitcher already handles both.
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
_ELEVENLABS_DIR = "/tmp/tabloid_vo_elevenlabs"


# Default ElevenLabs voice IDs by role. All are on the shared-voices library
# (free tier users can use them). Swap to your own cloned voices by setting
# ELEVENLABS_VOICE_<ROLE> env vars.
_ELEVEN_ROLE_VOICES = {
    "anchor":      ("EXAVITQu4vr4xnSDxMaL", "Sarah"),     # warm, measured female
    "provocateur": ("JBFqnCBsd6RMkjVDRZzb", "George"),    # sharp, assertive male
    "analyst":     ("onwK4e9ZLuTAKqWW03F9", "Daniel"),    # steady, clinical male
    "humanist":    ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),     # warm, compassionate female
}


def _voice_id(voice_params: dict[str, Any]) -> str:
    """Map our persona voice knobs to a Seed Speech voice_id."""
    gender = voice_params.get("gender", "female")
    warmth = voice_params.get("warmth", "medium")
    return f"{gender}_{warmth}"


async def generate_seed_speech(text: str, voice_params: dict[str, Any]) -> str:
    """Generate VO for a single line. Returns an audio URL or file:// path."""
    if settings().mock:
        return _mock_silent_clip(text)

    provider = settings().tts_provider
    if provider == "elevenlabs":
        return await _elevenlabs_tts(text, voice_params)
    # Default = byteplus
    return await _byteplus_seed_speech(text, voice_params)


# -- BytePlus Seed Speech ----------------------------------------------------

async def _byteplus_seed_speech(text: str, voice_params: dict[str, Any]) -> str:
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

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(f"{base}/tts", headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("Seed Speech %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        data = resp.json()
        if "audio_url" in data:
            return data["audio_url"]
        raise RuntimeError(f"Unexpected Seed Speech response shape: {list(data)}")


# -- ElevenLabs --------------------------------------------------------------

def _eleven_voice_for(voice_params: dict[str, Any]) -> str:
    role = voice_params.get("role") or voice_params.get("_role") or ""
    # Allow env-var override per role: ELEVENLABS_VOICE_ANCHOR=<voice_id>
    override = os.getenv(f"ELEVENLABS_VOICE_{role.upper()}", "").strip()
    if override:
        return override
    vid, _name = _ELEVEN_ROLE_VOICES.get(role, _ELEVEN_ROLE_VOICES["anchor"])
    return vid


async def _elevenlabs_tts(text: str, voice_params: dict[str, Any]) -> str:
    api_key = settings().elevenlabs_api_key
    if not api_key:
        raise RuntimeError("TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is unset")

    voice_id = _eleven_voice_for(voice_params)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": settings().elevenlabs_model,
        "voice_settings": {
            # Tabloid tone: a touch expressive, not monotone anchor.
            "stability": 0.4,
            "similarity_boost": 0.75,
            "style": 0.35,
            "use_speaker_boost": True,
        },
    }

    os.makedirs(_ELEVENLABS_DIR, exist_ok=True)
    out_path = os.path.join(_ELEVENLABS_DIR, f"vo_{uuid.uuid4().hex[:8]}.mp3")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("ElevenLabs %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(resp.content)

    return f"file://{out_path}"


# -- Mock --------------------------------------------------------------------

def _mock_silent_clip(text: str) -> str:
    """Silent mp3 for offline dev, length scaled to line length."""
    os.makedirs(_MOCK_DIR, exist_ok=True)
    path = os.path.join(_MOCK_DIR, f"vo_{uuid.uuid4().hex[:8]}.mp3")
    secs = max(1.5, min(6.0, len(text) / 15))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{secs:.2f}",
                "-c:a", "libmp3lame", "-b:a", "64k",
                path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        open(path, "wb").close()
    return f"file://{path}"
