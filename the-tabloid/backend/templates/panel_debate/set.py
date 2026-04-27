"""Panel-debate set extension.

A panel debate has ONE locked broadcast studio with one lighting state.
A song-video template would extend `SetLibrary` with multiple locations
and lighting states; this template's library has exactly one entry.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...sdk.types import ImageRef, LightingSpec, RenderStyle, SetRef


@dataclass
class BroadcastStudio:
    """The locked stage. A single master image + key/fill/back lighting
    state + the rendered style. Every keyframe in the show derives from
    this set."""
    id: str
    master_image: ImageRef
    lighting: LightingSpec
    style: RenderStyle

    # Identifying fixtures the storyboard prompts repeat verbatim so the
    # model doesn't redesign the set between scenes (e.g. "curved anchor
    # desk", "THE TABLOID backlight", "out-of-focus broadcast monitors").
    fixtures: tuple[str, ...] = ()

    @property
    def ref(self) -> SetRef:
        return SetRef(self.id)


@dataclass
class PanelDebateSetLibrary:
    """SetLibrary protocol with cardinality 1 — a panel debate is one
    studio across the whole episode."""
    studio: BroadcastStudio

    @property
    def sets(self) -> dict[str, BroadcastStudio]:
        return {self.studio.id: self.studio}
