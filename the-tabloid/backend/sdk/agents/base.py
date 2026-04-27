"""Agent base classes.

Every agent is a node in the production graph and a pure function of
(inputs, asset_registry). No hidden state. Re-running the same agent with
the same inputs produces the same output — this is what makes partial
regeneration safe.

The base classes are intentionally minimal protocols, not heavy ABCs. A
template's concrete agent just has to implement `run` (or its async twin)
with the right signature.

Pipeline order (some optional per template):

    research → structure → script → casting → set →
    director → storyboard → continuity → animator → editor

Templates pick which agents apply (writing pipeline is optional; production
pipeline is universal).
"""
from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from ..registry import AssetRegistry
from ..types import (
    Cast,
    Clip,
    ContinuityReport,
    Keyframe,
    Scene,
    Set,
    SetLibrary,
    ShotPlan,
)


P = TypeVar("P")


# -- Writing side ------------------------------------------------------------
#
# Optional per template. Some shows have hand-written scripts; the SDK lets
# templates skip these and feed Scene[P] objects in directly.

@runtime_checkable
class ResearchAgent(Protocol):
    """Ground the show in truth before scripting. Output shape is
    template-defined (a panel debate's briefing isn't a song's emotional
    arc). Returns a free-form dict — the script agent knows how to read it."""
    async def run(self, *, premise: str, goal: str, context: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class StructureAgent(Protocol):
    """Decide the shape of the show — the beat sheet."""
    async def run(self, *, premise: str, research: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class ScriptAgent(Protocol, Generic[P]):
    """Produce scenes with template-specific payload populated."""
    async def run(
        self, *, beats: dict[str, Any], research: dict[str, Any]
    ) -> list[Scene[P]]: ...


# -- Asset agents (run once per show) ---------------------------------------

@runtime_checkable
class CastingAgent(Protocol):
    """Build the never-degrading identity reference layer: anchor sheet +
    per-character sheets. Registers assets in the registry. Run once."""
    async def run(self, *, registry: AssetRegistry, **kwargs: Any) -> Cast: ...


@runtime_checkable
class SetAgent(Protocol):
    """Establish locked environments. Registers set master images in the
    registry. Run once."""
    async def run(self, *, registry: AssetRegistry, **kwargs: Any) -> SetLibrary: ...


# -- Production agents (run per scene) --------------------------------------

@runtime_checkable
class DirectorAgent(Protocol, Generic[P]):
    """Creative direction at the scene level. Picks featured cast, framing,
    motion intent. Outputs a ShotPlan."""
    async def run(self, *, scene: Scene[P], registry: AssetRegistry) -> ShotPlan: ...


@runtime_checkable
class StoryboardAgent(Protocol, Generic[P]):
    """Stage 1: turn ShotPlan into actual pixels via still-image model.
    Returns a Keyframe (and its registry record is updated). Templates fill
    in the prompt composition for their genre."""
    async def run(
        self,
        *,
        scene: Scene[P],
        plan: ShotPlan,
        registry: AssetRegistry,
        position: str,                # "start" | "end"
    ) -> Keyframe: ...


@runtime_checkable
class ContinuityAgent(Protocol, Generic[P]):
    """Verify consistency across keyframes/clips. Templates pick which
    checks to run. Run BEFORE expensive video generation when possible."""
    async def run(
        self,
        *,
        scene: Scene[P],
        registry: AssetRegistry,
    ) -> ContinuityReport: ...


@runtime_checkable
class AnimatorAgent(Protocol, Generic[P]):
    """Stage 2: animate between two locked keyframes via i2v with
    image_url + end_image_url. Templates fill in motion vocabulary
    (panel = locked + small gestures, song = slow dolly + emotion push)."""
    async def run(
        self,
        *,
        scene: Scene[P],
        plan: ShotPlan,
        start: Keyframe,
        end: Keyframe | None,
        registry: AssetRegistry,
    ) -> Clip: ...


@runtime_checkable
class EditorAgent(Protocol, Generic[P]):
    """Assemble ordered clips into the final cut. The interface is a
    timeline, not a flat concat list — designed so future capabilities
    (music, lower thirds, B-roll, trimming) slot in without rewriting
    the agent.

    Returns a dict with at least {output_path, duration_seconds, size_bytes}.
    Templates extend the timeline with genre-specific overlays."""
    async def run(
        self,
        *,
        scenes: list[Scene[P]],
        clips: list[Clip],
        registry: AssetRegistry,
        timeline_extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
