"""Panel-debate cast extension.

Subclasses the SDK's CastMember/Cast protocols with the fields a panel
debate needs:
  - seat_position: where they sit at the desk (0..3, left-to-right from
    the viewer's POV) — drives over-shoulder coverage and seat continuity
  - role: which of the four panel slots they fill — drives speaker
    selection in the director and the debate engine
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ...sdk.types import CastMemberRef, ImageRef, VoiceConfig


PanelRole = Literal["anchor", "provocateur", "analyst", "humanist"]


@dataclass
class Panelist:
    """One seat at the desk. The four-panelist cardinality is enforced by
    PanelCast; a single Panelist is just a member of that fixed group."""
    id: str
    name: str
    role: PanelRole
    seat_position: int                # 0..3, left-to-right from viewer POV
    character_sheet: ImageRef
    voice: VoiceConfig

    # Free-form natural-language description of what this person looks like.
    # Fed into the storyboard prompt so the model can lock identity in i2v
    # output. Kept short and distinctive (wardrobe colour, glasses, hair).
    visual_description: str = ""

    # Editorial knobs the script + debate agents read.
    lean: str = ""
    culture: str = ""
    style: str = ""

    @property
    def ref(self) -> CastMemberRef:
        return CastMemberRef(self.id)


@dataclass
class PanelCast:
    """Fixed four-seat panel with an anchor sheet (the group reference image
    every keyframe is anchored to). Cardinality is locked; the director
    picks one of these four as featured per scene."""
    show_id: str
    members: list[Panelist]
    anchor_sheet: ImageRef
    seat_labels: tuple[str, str, str, str] = (
        "far left", "center-left", "center-right", "far right",
    )

    def __post_init__(self) -> None:
        if len(self.members) != 4:
            raise ValueError(
                f"PanelCast requires exactly 4 panelists, got {len(self.members)}"
            )
        seats = sorted(p.seat_position for p in self.members)
        if seats != [0, 1, 2, 3]:
            raise ValueError(
                f"PanelCast seat_positions must be {{0,1,2,3}}, got {seats}"
            )

    def by_role(self, role: PanelRole) -> Panelist | None:
        return next((p for p in self.members if p.role == role), None)

    def by_seat(self, seat_position: int) -> Panelist | None:
        return next((p for p in self.members if p.seat_position == seat_position), None)

    def by_id(self, panelist_id: str) -> Panelist | None:
        return next((p for p in self.members if p.id == panelist_id), None)

    def seated_left_to_right(self) -> list[Panelist]:
        return sorted(self.members, key=lambda p: p.seat_position)
