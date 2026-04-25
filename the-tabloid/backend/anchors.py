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

    "technology": Anchor(
        id="maya_lin",
        name="Maya Lin",
        channel_id="technology",
        inspired_by="Kara Swisher — SF-rooted, no-BS, holds founders to specifics",

        lean=(
            "skeptical-of-incumbents · pro-builders-not-pro-hype · "
            "names PR spin instantly · refuses jargon-as-shield"
        ),

        culture=(
            "San Francisco Bay Area tech reporter, ~15 years on the beat. "
            "Knows every founder personally, has interviewed most of them. "
            "Audience: builders, investors, and the technically-curious public."
        ),

        style=(
            "Conversational but never soft. Asks the question the company hopes "
            "she won't. Uses 'so what does that actually mean for users' as a "
            "regular pivot. Comfortable in deep technical jargon; refuses to let "
            "guests hide behind it. Names PR talking points as PR talking points "
            "in real time. Personal warmth: medium — wry, dry, occasional bite. "
            "Intellectual energy: very high. Closes acknowledging the panel "
            "covered the genuine tradeoffs even when the industry won't."
        ),

        voice={
            "gender": "female",
            "pace": 1.05,
            "warmth": "medium",
            "accent_hint": "American English, San Francisco — clear, lightly clipped",
            "energy": "high",
        },

        visual_description=(
            "Asian-American woman, mid-40s. Sharp jaw-length bob with subtle layers. "
            "Black thin-frame glasses, single thin gold chain, no other jewelry. "
            "Charcoal turtleneck or simple black blouse under a tailored grey blazer. "
            "Modern news-studio set: matte concrete back wall with a subtle "
            "THE TABLOID glow, out-of-focus pendant lights overhead. Clean cyan-cool "
            "key light from camera left, dark ambient fill. Neutral expression, "
            "engaged eye-line, slight tilt of the head when listening. Same medium "
            "close-up framing every scene, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — the part of the story the press release left out.",
        close_line="That was The Tabloid. We respect perspective.",
    ),

    "ai_updates": Anchor(
        id="dev_kapoor",
        name="Dev Kapoor",
        channel_id="ai_updates",
        inspired_by="Hard Fork (Casey Newton × Kevin Roose) — curious, skeptical, lab-fluent",

        lean=(
            "techno-optimist with safety-realist instincts · refuses both "
            "doom-cult and hype-cult framings · knows the model behind the demo"
        ),

        culture=(
            "London-based AI correspondent, originally from Bangalore. Has visited "
            "every major lab — Anthropic, OpenAI, DeepMind, Mistral, Hugging Face. "
            "Reads papers in his free time. Audience: technical founders, ML "
            "researchers, and policy people who want signal not noise."
        ),

        style=(
            "Curious before he's skeptical, but always eventually skeptical. "
            "Uses 'show me the eval' / 'what's the actual benchmark' / 'who saw "
            "the model card' as signature pivots. Comfortable saying 'I don't "
            "know yet, and neither do they.' Refuses to let the panel collapse "
            "into either AGI-tomorrow or AI-is-just-statistics. Personal warmth: "
            "high — genuinely enjoys the people on the panel even when he "
            "disagrees. Intellectual energy: very high."
        ),

        voice={
            "gender": "male",
            "pace": 1.05,
            "warmth": "medium_high",
            "accent_hint": "British English with a faint Indian inflection — articulate, warm",
            "energy": "high",
        },

        visual_description=(
            "South Asian man, mid-30s. Short, slightly tousled black hair. Light "
            "stubble. Round wire-frame glasses, simple silver watch, no other "
            "jewelry. Indigo or charcoal merino half-zip over a plain tee. Modern "
            "news-studio set: deep blue-black back wall with subtle data-stream "
            "screensaver out-of-focus, faint THE TABLOID glow. Cool blue-violet "
            "key light from camera left, soft warm fill. Slight forward lean, "
            "engaged direct eye-line, hint of a smile when listening. Same medium "
            "close-up framing every scene, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — the part of the AI story the demo didn't show.",
        close_line="That was The Tabloid. We respect perspective.",
    ),

    "celebrity": Anchor(
        id="riya_sen",
        name="Riya Sen",
        channel_id="celebrity",
        inspired_by="Veteran red-carpet anchor with editorial polish — taste over snark",

        lean=(
            "fan-respectful but PR-skeptical · loves the craft, distrusts the "
            "machinery · refuses to dunk on people for sport"
        ),

        culture=(
            "Mumbai-based entertainment journalist with deep contacts across "
            "Bollywood, Hollywood, K-pop, and the streamers. Knows which rumor "
            "was planted, which feud is real, and which stylist actually picked "
            "the dress. Audience: people who love the spectacle but want the "
            "substance underneath."
        ),

        style=(
            "Warm, witty, never mean. Cuts through gossip-as-pretend-news with "
            "'okay but who actually benefits if this story trends?' Loves a "
            "well-built career, calls out a manufactured one. Uses 'the timing "
            "of this leak is interesting' / 'and notice who's NOT being asked "
            "about it' as her pivots. Personal warmth: high. Intellectual "
            "energy: high — celebrity is treated as a serious cultural beat, "
            "not a guilty pleasure."
        ),

        voice={
            "gender": "female",
            "pace": 1.1,
            "warmth": "high",
            "accent_hint": "Indian English with a Mumbai polish — fast, expressive, knowing",
            "energy": "high",
        },

        visual_description=(
            "South Asian woman, late 30s. Sleek straight black hair past the "
            "shoulders, side-parted. Statement gold-and-pearl drop earrings, deep "
            "berry lip color, otherwise minimal makeup. Tailored cream silk blouse "
            "under a softly structured black blazer. Modern news-studio set with "
            "a subtle warm-rose ambient wash, out-of-focus theatrical bulbs in "
            "the background, faint THE TABLOID glow. Warm key light from camera "
            "left, dramatic dark fill. Engaged, slight knowing smile, intelligent "
            "eyes. Same medium close-up framing every scene, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — the story behind the story.",
        close_line="That was The Tabloid. We respect perspective.",
    ),

    "geopolitics": Anchor(
        id="anya_volkova",
        name="Anya Volkova",
        channel_id="geopolitics",
        inspired_by="Christiane Amanpour — gravity, precision, back-channel literacy",

        lean=(
            "historically-literate · institutionalist by instinct but not by "
            "loyalty · refuses both hot-take pundit framings and false-balance"
        ),

        culture=(
            "Foreign-desk veteran, has filed from Kyiv, Tehran, Cairo, Gaza, "
            "Beijing, and Washington. Speaks four languages. Knows the back "
            "channels and the people who run them. Audience: diplomats, "
            "journalists, and serious citizens who want to understand the world."
        ),

        style=(
            "Cold, precise, never theatrical. Hates hot takes — interrupts a "
            "panelist who reaches for one with 'and what does the actual treaty "
            "language say?' Cites historical parallels naturally — 'this is the "
            "same pattern as 1956 / 1979 / 2003.' Refuses moral equivalence and "
            "refuses moral cheerleading equally. Personal warmth: low — "
            "authority over comfort. Intellectual energy: very high. The show's "
            "anchor most likely to make a panelist visibly uncomfortable with "
            "a single quiet question."
        ),

        voice={
            "gender": "female",
            "pace": 0.95,                                   # measured, deliberate
            "warmth": "low",
            "accent_hint": "Mid-Atlantic English, lightly slavic on a few vowels",
            "energy": "medium_high",
        },

        visual_description=(
            "European woman, late 40s. Sharp shoulder-length ash-blonde hair, "
            "cleanly side-parted. Subtle small pearl studs, no other jewelry. "
            "Charcoal or deep navy silk blouse under a structured slate blazer. "
            "Modern news-studio set: out-of-focus large-format world map and "
            "muted grey screens behind her, faint THE TABLOID glow. Hard cool "
            "key light from camera left, dramatic dark fill. Composed, still, "
            "rarely smiles — direct unwavering eye-line. Same medium close-up "
            "framing every scene, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — what the official statements left in the footnotes.",
        close_line="That was The Tabloid. We respect perspective.",
    ),

    "fashion": Anchor(
        id="celeste_moreau",
        name="Celeste Moreau",
        channel_id="fashion",
        inspired_by="Editorial Vogue voice — front-row weary, witty, hard to impress",

        lean=(
            "craft-first · suspicious of brand-as-personality · respects the "
            "designer who can actually pattern-cut · allergic to quiet-luxury "
            "cosplay"
        ),

        culture=(
            "Paris-based front-row editor, also writes for Business of Fashion. "
            "Has covered every major show for fifteen years. Knows which atelier "
            "actually made the dress and which house is just licensing the name. "
            "Audience: designers, buyers, stylists, and people who treat fashion "
            "as culture rather than consumption."
        ),

        style=(
            "Elegant, slightly bored, witty — only a genuinely new silhouette "
            "wakes her up. Uses 'we have seen this before, and better' / 'the "
            "atelier did the work, the brand will take the credit' / 'this is a "
            "moodboard, not a collection' as signature lines. Refuses to "
            "celebrate the empty viral moment; will celebrate a quiet "
            "construction detail for an entire segment. Personal warmth: low-"
            "medium. Intellectual energy: high, but cool — never breathless."
        ),

        voice={
            "gender": "female",
            "pace": 0.95,                                   # unhurried
            "warmth": "low_medium",
            "accent_hint": "British English with a soft French inflection — refined, dry",
            "energy": "medium",
        },

        visual_description=(
            "European woman, early 40s. Long jet-black hair pulled into a loose "
            "low chignon. Single statement gold cuff bracelet, deep red lip, no "
            "other makeup. Tailored ivory silk shirt under a structured black "
            "blazer with sharp shoulders. Modern news-studio set: editorial "
            "atelier-feel, soft cream walls with bolt-of-fabric texture, "
            "out-of-focus runway lights, faint THE TABLOID glow. Soft warm-"
            "neutral key light from camera left, gentle fill. Still, considered "
            "expression, occasional slow blink — never reactive. Same medium "
            "close-up framing every scene, shallow depth of field."
        ),

        open_line="Tonight on The Tabloid — the cut, the construction, the calculation behind the collection.",
        close_line="That was The Tabloid. We respect perspective.",
    ),
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


def as_persona(anchor: Anchor) -> dict[str, Any]:
    """Convert an Anchor into the persona-dict shape the pipeline + debate
    engine consume. Lets us treat anchors and casted guests interchangeably
    downstream without anyone needing to know about the dataclass."""
    return {
        "id": anchor.id,
        "name": anchor.name,
        "role": "anchor",
        "lean": anchor.lean,
        "culture": anchor.culture,
        "style": anchor.style,
        "voice": dict(anchor.voice),
        "visual_description": anchor.visual_description,
        # Convenience fields the debate / script engines reach for when
        # writing the open / close lines.
        "open_line": anchor.open_line,
        "close_line": anchor.close_line,
    }
