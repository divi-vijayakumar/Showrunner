"""Generic Pipeline orchestrator skeleton.

The pipeline shape is universal across templates:

    research → structure → script → casting → set →
    [per scene: director → storyboard → continuity → animator] →
    editor

What flows through it is genre-specific (the `Scene.payload` and the
agent override decisions).

Status: skeleton. The current panel-debate template runs its pipeline
inline in `templates/panel_debate/pipelines/direct.py` (legacy procedural
shape). This module is the target for the future cleanly-typed
orchestrator that templates configure with their concrete agents. As
each agent is migrated to the typed contract, it'll be wired through here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .agents.base import (
    AnimatorAgent,
    CastingAgent,
    ContinuityAgent,
    DirectorAgent,
    EditorAgent,
    ResearchAgent,
    ScriptAgent,
    SetAgent,
    StoryboardAgent,
    StructureAgent,
)
from .registry import AssetRegistry
from .types import Scene


P = TypeVar("P")


@dataclass
class TemplateAgents(Generic[P]):
    """The set of concrete agents a template wires up. Writing-side agents
    are optional — a template with hand-written scripts leaves them None
    and feeds Scene[P] objects in directly."""

    # Writing pipeline (optional)
    research: ResearchAgent | None = None
    structure: StructureAgent | None = None
    script: ScriptAgent[P] | None = None

    # Asset pipeline (run once per show)
    casting: CastingAgent | None = None
    set: SetAgent | None = None

    # Production pipeline (per scene)
    director: DirectorAgent[P] | None = None
    storyboard: StoryboardAgent[P] | None = None
    continuity: ContinuityAgent[P] | None = None
    animator: AnimatorAgent[P] | None = None
    editor: EditorAgent[P] | None = None


class Pipeline(Generic[P]):
    """Orchestrator. A template instantiates this with its TemplateAgents
    and calls `produce_episode(scenes)`.

    This is the *target* shape. The first migration pass moves the existing
    panel-debate procedural pipeline into a place where it can be progressively
    rewritten in this form, agent by agent."""

    def __init__(self, agents: TemplateAgents[P], registry: AssetRegistry) -> None:
        self.agents = agents
        self.registry = registry

    async def produce_episode(
        self, scenes: list[Scene[P]], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run the production pipeline over a list of pre-written scenes.

        Currently a placeholder — the panel_debate template still runs its
        legacy procedural body. As agents migrate to the typed contract,
        this method becomes the single orchestrator."""
        raise NotImplementedError(
            "Pipeline.produce_episode is the future entry point; templates "
            "currently still run inline pipelines under templates/*/pipelines/. "
            "Migration is incremental — see MOVIE_SDK.md build order."
        )
