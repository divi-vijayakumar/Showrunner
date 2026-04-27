"""PanelDebateTemplate — the first reference template on top of the SDK.

Genre: a four-seat panel debating a single news beat. One locked studio,
one fixed cast, alternating-anchor i2v keyframe chain, native-audio
lip-synced VO via Seedance.

Layer 2 of the three-layer model. Subclasses the SDK abstractions:

  CastMember  → Panelist (seat_position, role)
  Cast        → PanelCast (fixed 4 seats)
  Set         → BroadcastStudio (single locked set)
  Scene[P]    → Scene[PanelScenePayload] (featured_speaker, spoken_lines, beat)

The agents in `templates/panel_debate/agents/` are the genre-specific
overrides. The pipeline in `templates/panel_debate/pipeline.py` is the
production runner that wires those agents together (currently as a
single procedural file; future work splits it per-mode and migrates each
agent to the typed sdk/agents/base.py contract).

A second show on this template would re-use everything here and ship
its own `shows/<show_id>/` cast + set instances.
"""
from __future__ import annotations

from .cast import PanelCast, Panelist, PanelRole
from .scene import PanelScenePayload, SceneBeatType, SpokenLine
from .set import BroadcastStudio, PanelDebateSetLibrary

__all__ = [
    "Panelist",
    "PanelCast",
    "PanelRole",
    "BroadcastStudio",
    "PanelDebateSetLibrary",
    "PanelScenePayload",
    "SceneBeatType",
    "SpokenLine",
]
