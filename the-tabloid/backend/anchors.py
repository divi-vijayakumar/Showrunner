"""Channel anchors — the persistent host of each Tabloid channel.

Anchors are the recognizable face of the show. One per channel, fixed across
episodes. Their portrait is generated once via Seedream and cached forever;
their voice is mapped via the TTS provider abstraction.

Guests are NOT here — they're cast on-the-fly per story by `casting.py`. This
file is the only persona pool in the system.

Adding a new channel: define an Anchor entry, give them a strong editorial
identity, and ship.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Anchor:
    id: str
    name: str
    channel_id: str

    # Internal note about creative inspiration. NEVER passed to any LLM or
    # video model — only `name`, `style`, `visual_description`, etc. travel
    # downstream.
    inspired_by: str

    lean: str
    culture: str
    style: str

    # Provider-agnostic voice knobs. TTS adapters (ElevenLabs, Gemini TTS,
    # Seed Speech) map these to their own concepts.
    voice: dict[str, Any]

    # Frozen prompt fragment fed verbatim into Seedream (for the persistent
    # portrait) and Seedance (so every shot of this anchor matches).
    visual_description: str

    open_line: str
    close_line: str


ANCHORS: dict[str, Anchor] = {
    "india_politics": Anchor(
        id="veera_naatchi",
        name="Veera Naatchi",
        channel_id="india_politics",
        inspired_by="Palki Sharma — sharp, strategic, modern Indian broadcaster",

        lean=(
            "centrist · India-first global lens · refuses balance-for-balance's-sake · "
            "calls out spin from any side"
        ),

        culture=(
            "Modern Indian English broadcaster — culturally rooted, globally fluent. "
            "Broadcast-polished Indian English with a faint Tamil register. "
            "Audience: urban India + the diaspora."
        ),

        style=(
            "Sharp. Strategic. Swag. Opens by naming the question viewers are actually "
            "asking, then dissects it — who benefits, what's the historical pattern, "
            "what's the spin. Decoder energy: 'Let me decode this for you' / 'Here's "
            "what's actually happening' / 'And here's the part nobody's reporting.' "
            "No hedging. Names spin from any side. Interrupts panelists who drift into "
            "talking points; demands specifics. Intellectual energy: very high. "
            "Personal warmth: low-medium — authority, not maternal. "
            "Signs off acknowledging that the panel held different views in good "
            "faith — the show's posture is that disagreement deserves space, even "
            "while she calls out spin within it."
        ),

        voice={
            "gender": "female",
            "pace": 1.15,                                  # ~15% faster than baseline
            "warmth": "low_medium",                        # authoritative, not maternal
            "accent_hint": "Indian English, broadcast-polished, faint Tamil register",
            "energy": "high",
        },

        visual_description=(
            "Modern Indian woman, late 30s. Sharp shoulder-length straight black hair "
            "with a confident side part. One statement gold ear-cuff, otherwise minimal "
            "jewelry. Tailored structured blazer in a strong color — deep burgundy, ink "
            "black, or forest green — over a crisp white shell or silk camisole. Bold "
            "lip color (signature). Modern news-studio set: out-of-focus screens behind "
            "her, subtle THE TABLOID glow on the back wall. Strong key light from "
            "camera left, soft fill, dark studio background. Composed, slightly forward-"
            "leaning posture. Direct, intelligent gaze. No smile in resting frame — "
            "strategic, not theatrical. Same framing every scene: medium close-up, "
            "eye-line locked to camera, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — let me decode this for you.",
        close_line="That was The Tabloid. We respect perspective.",
    ),

    # ------------------------------------------------------------------
    # TODO — define the remaining 5 channel anchors. Sketch / vibe notes:
    #
    # technology    — Kara-Swisher energy. SF-rooted, no-BS, holds founders
    #                 to specifics, comfortable in jargon but refuses to
    #                 hide behind it.
    # ai_updates    — Hard Fork's Casey-Newton-meets-Kevin-Roose tone.
    #                 Curious + skeptical. Knows the AI labs personally.
    # celebrity     — Tabloid-veteran energy with editorial polish. Knows
    #                 which rumor was planted, loves a juicy pivot.
    # geopolitics   — Christiane-Amanpour gravity. Cold, precise, hates
    #                 hot takes, knows the back channels.
    # fashion       — Editorial Vogue voice. Front-row weary, witty, only
    #                 a genuinely new silhouette wakes her up.
    #
    # When filling these in, keep the same Anchor schema and write a strong
    # signature open_line + close_line for each — the close_line is what
    # the model uses to wrap every episode.
    # ------------------------------------------------------------------
}


def for_channel(channel_id: str) -> Anchor:
    """Return the anchor for a channel, raising if undefined."""
    if channel_id not in ANCHORS:
        raise KeyError(
            f"No anchor defined for channel {channel_id!r}. "
            f"Add one to ANCHORS in backend/anchors.py."
        )
    return ANCHORS[channel_id]


def has_anchor(channel_id: str) -> bool:
    return channel_id in ANCHORS
