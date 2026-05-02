"""Asset Registry — typed, versioned, queryable index of every asset in a show.

Agents query the registry by typed Ref (CastMemberRef, SetRef, ImageRef,
KeyframeRef, ClipRef), never by raw filename. When an asset is regenerated,
its version bumps; downstream artifacts that depend on the old version are
flagged stale.

This is the universal layer. Templates use the same registry; only the
*types* of assets registered differ.

For now this is a thin in-memory + manifest-backed implementation. The
panel-debate pipeline currently writes per-segment `manifest.json` files
under `data/segments/{seg_id}/` — those manifests ARE the registry for
that episode. Future work: promote to a process-wide singleton with
content-hash-based versioning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import (
    CastMemberRef,
    ClipRef,
    ImageRef,
    KeyframeRef,
    SetRef,
)


@dataclass
class AssetRecord:
    """One asset's metadata. The actual bytes live elsewhere (Seedance CDN,
    Seedream CDN, local disk under data/, Firebase Storage). The registry
    just indexes them.

    `version` bumps on every regeneration; `compatible_with` lists asset
    IDs this asset is known-good with (e.g. a character sheet is compatible
    with a specific anchor sheet version)."""
    id: str
    type: str                          # cast_anchor | character_sheet | set_master | keyframe | clip | ...
    location: str                      # URL or local path
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    compatible_with: list[str] = field(default_factory=list)


class AssetRegistry:
    """In-memory registry. One per show or one per episode — caller decides.

    The methods are kept narrow on purpose. The point isn't fancy querying;
    it's *typed addressing* of assets so downstream agents stop hard-coding
    URLs and filenames."""

    def __init__(self) -> None:
        self._records: dict[str, AssetRecord] = {}

    # --- write side -----------------------------------------------------

    def register(self, record: AssetRecord) -> None:
        """Add or replace a record. Replacing bumps the version unless the
        caller already set a higher one."""
        existing = self._records.get(record.id)
        if existing and record.version <= existing.version:
            record.version = existing.version + 1
        self._records[record.id] = record

    def remove(self, asset_id: str) -> None:
        self._records.pop(asset_id, None)

    # --- read side ------------------------------------------------------

    def get(self, asset_id: str) -> AssetRecord | None:
        return self._records.get(asset_id)

    def resolve_image(self, ref: ImageRef) -> AssetRecord | None:
        return self.get(ref.id)

    def resolve_cast(self, ref: CastMemberRef) -> AssetRecord | None:
        return self.get(f"cast:{ref.id}")

    def resolve_set(self, ref: SetRef) -> AssetRecord | None:
        return self.get(f"set:{ref.id}")

    def resolve_keyframe(self, ref: KeyframeRef) -> AssetRecord | None:
        return self.get(f"keyframe:{ref.id}")

    def resolve_clip(self, ref: ClipRef) -> AssetRecord | None:
        return self.get(f"clip:{ref.id}")

    def by_type(self, asset_type: str) -> list[AssetRecord]:
        return [r for r in self._records.values() if r.type == asset_type]

    def all(self) -> list[AssetRecord]:
        return list(self._records.values())
