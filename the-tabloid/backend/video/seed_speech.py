"""Text-to-speech wrapper.

Dispatches by TTS_PROVIDER env:
- byteplus (default): BytePlus Seed Speech (real hackathon path)
- elevenlabs:         ElevenLabs v2 multilingual — drop-in when Seed is unavailable

Returns either a URL the stitcher can fetch, or a `file://` path to audio we
wrote locally. The stitcher already handles both.
"""
from __future__ import annotations

import base64
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
_GOOGLE_DIR = "/tmp/tabloid_vo_google"


# ElevenLabs voice library, keyed by (culture_bucket, gender). Each persona
# gets assigned to the closest bucket by keyword match on its `culture` field.
# All IDs below are from ElevenLabs' shared-voices library.
#
# Override any single persona at runtime with ELEVENLABS_VOICE_<ROLE>=<id>.

_ELEVEN_LIB: dict[tuple[str, str], tuple[str, str]] = {
    # Indian / South Asian English
    ("india", "female"):       ("MF3mGyEYCl7XYWbV9V6O", "Elli-IN"),
    ("india", "male"):          ("AZnzlk1XvdvUeBnXmlld", "Adam-desi"),
    # UK English
    ("uk",    "female"):       ("oWAxZDx7w5VEj9dCyTzz", "Grace-UK"),
    ("uk",    "male"):          ("pNInz6obpgDQGcFmaJgB", "Adam-UK"),
    # US English
    ("us",    "female"):       ("21m00Tcm4TlvDq8ikWAM", "Rachel-US"),
    ("us",    "male"):          ("TxGEqnHWrfWFTfGW9XjX", "Josh-US"),
    # Warm / compassionate (humanist default)
    ("warm",  "female"):       ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),
    ("warm",  "male"):          ("bIHbv24MWmeRgasZH58o", "Will"),
    # Sharp / assertive (provocateur default)
    ("sharp", "male"):          ("JBFqnCBsd6RMkjVDRZzb", "George"),
    ("sharp", "female"):        ("XrExE9yKIg1WjnnlVkGX", "Matilda"),
    # Middle Eastern / Arabic-accented English
    ("arab",  "female"):       ("ThT5KcBeYPX3keUQqHPh", "Dorothy-ME"),
    ("arab",  "male"):          ("N2lVS1w4EtoT3dr4eOWO", "Callum-ME"),
    # Latin American English
    ("latin", "female"):       ("AZnzlk1XvdvUeBnXmlld", "Domi"),
    ("latin", "male"):          ("5Q0t7uMcjvnagumLfvZi", "Paul"),
    # Generic fallback
    ("default", "female"):      ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
    ("default", "male"):        ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
    ("default", "nonbinary"):   ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),
}


def _culture_bucket(culture: str, role: str) -> str:
    """Pick a voice library bucket from a persona's `culture` string.
    Keyword-match so new personas don't require code changes."""
    c = (culture or "").lower()
    if any(k in c for k in ("tamil", "madurai", "chennai", "india", "delhi", "mumbai", "bangalore", "hyderabad")):
        return "india"
    if any(k in c for k in ("bbc", "reuters", "uk", "london", "britain", "england")):
        return "uk"
    if any(k in c for k in ("cairo", "arab", "mid-east", "middle east", "egypt", "palestine")):
        return "arab"
    if any(k in c for k in ("berlin", "latin", "mexico", "buenos", "madrid", "phoenix")):
        # Phoenix isn't latin-coded but our Phoenix persona is a rideshare driver — latin voice fits vibe better than white US male.
        return "latin" if "phoenix" not in c else "us"
    if any(k in c for k in ("sf", "nyu", "ny", "la ", "los angeles", "silicon", "bay area", "washington", "dc ", "ohio", "arizona", "us ")):
        return "us"
    # Role-based fallback colours the "generic" bucket
    if role == "humanist":
        return "warm"
    if role == "provocateur":
        return "sharp"
    return "default"


def _voice_id(voice_params: dict[str, Any]) -> str:
    """Byteplus Seed Speech voice_id (unused when TTS_PROVIDER=elevenlabs)."""
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
    if provider == "google":
        return await _google_tts(text, voice_params)
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
    role = (voice_params.get("role") or "").lower()
    gender = (voice_params.get("gender") or "female").lower()
    culture = voice_params.get("culture", "")

    # Per-role override wins: ELEVENLABS_VOICE_ANCHOR=<id>
    override = os.getenv(f"ELEVENLABS_VOICE_{role.upper()}", "").strip()
    if override:
        return override

    bucket = _culture_bucket(culture, role)
    vid_name = _ELEVEN_LIB.get((bucket, gender))
    if not vid_name:
        # Gender fallback within the bucket, then generic default.
        alt_gender = "male" if gender != "male" else "female"
        vid_name = _ELEVEN_LIB.get((bucket, alt_gender)) or _ELEVEN_LIB[("default", gender)]
    return vid_name[0]


def _eleven_voice_settings(voice_params: dict[str, Any]) -> dict[str, Any]:
    """Role-tuned voice settings. Broadcast-expressive, not monotone."""
    role = (voice_params.get("role") or "").lower()
    pace = float(voice_params.get("pace", 1.0))
    if role == "provocateur":
        # Sharp, urgent, punchy
        return {"stability": 0.25, "similarity_boost": 0.8, "style": 0.75, "use_speaker_boost": True}
    if role == "humanist":
        # Warm, personal, emotional swing
        return {"stability": 0.35, "similarity_boost": 0.8, "style": 0.65, "use_speaker_boost": True}
    if role == "analyst":
        # Measured but not flat — steady authority
        return {"stability": 0.45, "similarity_boost": 0.8, "style": 0.45, "use_speaker_boost": True}
    if role == "anchor":
        # Presenter energy: engaged, confident
        return {"stability": 0.35, "similarity_boost": 0.85, "style": 0.6, "use_speaker_boost": True}
    _ = pace  # ElevenLabs doesn't have a pace knob on this endpoint; prompt-level pacing controls it
    return {"stability": 0.4, "similarity_boost": 0.8, "style": 0.55, "use_speaker_boost": True}


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
        "voice_settings": _eleven_voice_settings(voice_params),
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


# -- Google Gemini TTS -------------------------------------------------------
# Gemini has ~30 prebuilt voices. We pick one by (role, gender) and steer
# accent/pace with an instruction prefix, which Gemini TTS understands and
# applies to the spoken text.

_GEMINI_VOICES: dict[tuple[str, str], str] = {
    ("anchor",      "female"):    "Kore",      # warm, measured
    ("anchor",      "male"):      "Zephyr",    # composed
    ("anchor",      "nonbinary"): "Puck",
    ("provocateur", "male"):      "Charon",    # deep, authoritative
    ("provocateur", "female"):    "Sulafat",   # crisp, assertive
    ("provocateur", "nonbinary"): "Puck",
    ("analyst",     "male"):      "Orus",      # steady, clinical
    ("analyst",     "female"):    "Leda",      # measured
    ("analyst",     "nonbinary"): "Umbriel",
    ("humanist",    "female"):    "Aoede",     # warm, emotional
    ("humanist",    "male"):      "Algieba",   # grounded
    ("humanist",    "nonbinary"): "Laomedeia",
}

# Accent hint by culture keyword — Gemini accepts natural-language steering.
_ACCENT_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("tamil", "madurai", "chennai", "india", "delhi", "mumbai", "bangalore", "hyderabad"),
     "Speak with an Indian English accent"),
    (("bbc", "reuters", "london", "uk ", "britain", "england"),
     "Speak with a British English accent"),
    (("cairo", "arab", "mid-east", "middle east", "egypt", "palestine", "israel"),
     "Speak with a subtle Middle-Eastern English accent"),
    (("berlin", "germany"),
     "Speak with a slight German English accent"),
    (("mexico", "buenos", "madrid", "latin"),
     "Speak with a Latin American English accent"),
    (("paris", "france"),
     "Speak with a slight French English accent"),
]


def _accent_hint(culture: str) -> str:
    c = (culture or "").lower()
    for keywords, hint in _ACCENT_HINTS:
        if any(k in c for k in keywords):
            return hint
    return ""


def _gemini_style_hint(role: str, pace: float) -> str:
    base = {
        "anchor":      "engaged and clear, presenting for broadcast",
        "provocateur": "sharp and urgent, slightly combative",
        "analyst":     "measured and authoritative, never flat",
        "humanist":    "warm and emotionally grounded, personal",
    }.get(role, "natural and expressive")
    tempo = "slightly faster than average" if pace >= 1.05 else (
        "slightly slower than average" if pace <= 0.95 else "at a natural pace"
    )
    return f"{base}, {tempo}"


def _gemini_voice_for(voice_params: dict[str, Any]) -> str:
    role = (voice_params.get("role") or "").lower()
    gender = (voice_params.get("gender") or "female").lower()
    override = os.getenv(f"GEMINI_VOICE_{role.upper()}", "").strip()
    if override:
        return override
    return _GEMINI_VOICES.get(
        (role, gender),
        _GEMINI_VOICES.get((role, "female"), "Kore"),
    )


def _extract_gemini_audio(data: dict[str, Any]) -> tuple[str | None, str]:
    """Return (base64_audio, finish_reason). Scans ALL parts across candidates
    — Gemini sometimes splits text + audio across multiple parts, and a
    safety-filtered response has no audio at all."""
    candidates = data.get("candidates") or []
    for cand in candidates:
        finish = cand.get("finishReason", "")
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return inline["data"], finish
    # No audio in any part — return the finishReason of the first candidate
    # so the caller can log why.
    finish = (candidates[0].get("finishReason") if candidates else "") or "UNKNOWN"
    return None, finish


async def _call_gemini_tts(text: str, voice: str, api_key: str, model: str) -> bytes | None:
    """Return raw PCM bytes for `text`, or None if Gemini returned no audio."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            log.error("Gemini TTS %s: %s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
        data = resp.json()

    b64, finish = _extract_gemini_audio(data)
    if not b64:
        log.warning("Gemini TTS returned no audio (finish=%s). text=%r", finish, text[:120])
        return None
    return base64.b64decode(b64)


async def _google_tts(text: str, voice_params: dict[str, Any]) -> str:
    api_key = settings().google_aistudio_api_key
    if not api_key:
        raise RuntimeError("TTS_PROVIDER=google but GOOGLE_AISTUDIO_API_KEY is unset")

    role = (voice_params.get("role") or "").lower()
    culture = voice_params.get("culture", "")
    pace = float(voice_params.get("pace", 1.0))
    voice = _gemini_voice_for(voice_params)
    model = settings().google_tts_model

    # Attempt 1: with accent + style directive — Gemini often applies it cleanly.
    accent = _accent_hint(culture)
    style = _gemini_style_hint(role, pace)
    directive = f"{accent}, {style}" if accent else style
    steered = f"{directive}: {text}"

    pcm = await _call_gemini_tts(steered, voice, api_key, model)

    # Attempt 2: safety filter sometimes trips on the instruction prefix
    # ("combative" etc.). Retry with just the raw text.
    if pcm is None:
        log.info("Gemini TTS retry without directive")
        pcm = await _call_gemini_tts(text, voice, api_key, model)

    os.makedirs(_GOOGLE_DIR, exist_ok=True)
    out_path = os.path.join(_GOOGLE_DIR, f"vo_{uuid.uuid4().hex[:8]}.mp3")

    if pcm is None:
        # Last-ditch: produce a silent placeholder so one bad line doesn't
        # brick a 16-line podcast. The speaker's beat still plays, just quiet.
        log.warning("Gemini TTS permanently failed for line — inserting silent beat")
        secs = max(1.5, min(6.0, len(text) / 15))
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{secs:.2f}",
                "-codec:a", "libmp3lame", "-b:a", "128k",
                out_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return f"file://{out_path}"

    # Wrap the raw PCM stream into mp3.
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "s16le", "-ar", "24000", "-ac", "1",
            "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-b:a", "128k",
            out_path,
        ],
        input=pcm,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg PCM→mp3 failed: {proc.stderr.decode()[-400:]}")

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
