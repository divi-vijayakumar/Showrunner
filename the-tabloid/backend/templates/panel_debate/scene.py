"""Panel-debate scene payload — the genre-specific content of a Scene.

A panel scene is one beat of debate: one panelist speaks (or two cross-
talk), the others react. `Scene[PanelScenePayload]` is what the director,
storyboard, and animator agents work over.

This is the extension point the SDK's `Scene[P]` generic exists for.
A song-video template defines `SongScenePayload(lyrics, emotion, ...)`;
a cooking-show template defines `RecipeScenePayload(step, technique, ...)`.
The base agents work on `Scene[P]` generically; what differs per genre
is what `P` carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ...sdk.types import CastMemberRef


SceneBeatType = Literal[
    "open",         # the anchor cold-opens the segment
    "frame",        # establish the question / stakes
    "first_take",   # opening positions from each panelist
    "rebuttal",     # someone pushes back on a previous take
    "evidence",     # data / source / pull-quote moment
    "clash",        # multi-speaker disagreement, head-to-head
    "synthesis",    # anchor names what the panel actually agreed on
    "close",        # sign-off
    "cross",        # short cross-talk between two panelists
]


@dataclass
class SpokenLine:
    """One line of dialogue with the speaker bound + a strict word/duration
    budget. The script agent populates these; the animator agent passes
    them to Seedance with `generate_audio=true` for native lip-sync."""
    speaker: CastMemberRef
    text: str
    duration_seconds: float


@dataclass
class PanelScenePayload:
    """What lives inside a `Scene[PanelScenePayload]`.

    Mirrors the dict shape currently produced by script_compiler.py and
    consumed by the direct-mode pipeline — but typed, so the future
    director/storyboard/animator agents can work over it without dict
    archaeology.
    """
    featured_speaker: CastMemberRef | None        # None = ensemble shot
    featured_role: str = ""                        # convenience for selectors
    spoken_lines: list[SpokenLine] = field(default_factory=list)
    beat: SceneBeatType = "first_take"

    # Free-form natural-language motion direction the storyboard/animator
    # consume verbatim (e.g. "slow dolly-in on Veera's face as she
    # delivers the open"). Genre-neutral motion grammar lives upstream
    # in CameraSpec; this is the writer's intent.
    motion_intent: str = ""

    # Title / shot label for the AgentLog UI.
    title: str = ""
    emotional_beat: str = ""
