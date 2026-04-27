"""Core typed artifacts that flow between agents.

The SDK's contract is *types*, not free text. Every agent emits and consumes
these. Templates extend `CastMember`, `Cast`, `Set`, and the `Scene.payload`
generic with genre-specific fields.

Refs (CastMemberRef, SetRef, KeyframeRef, ClipRef) are how agents address
assets in the registry — never by raw filename. Resolving a ref is the
asset registry's job.

This module intentionally avoids importing from anywhere else in `backend/`
so it stays the leaf of the dependency graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Protocol, TypeVar, runtime_checkable


# -- Refs --------------------------------------------------------------------
#
# Refs are typed handles into the asset registry. An agent receives e.g. a
# `CastMemberRef("veera")` and asks the registry to resolve it to a
# concrete sheet image + voice config. When the underlying asset is
# regenerated, only the registry changes — the ref stays the same.

@dataclass(frozen=True)
class ImageRef:
    """A reference to an image asset (URL or local path) in the registry."""
    id: str

@dataclass(frozen=True)
class CastMemberRef:
    """Reference to a cast member by stable id."""
    id: str

@dataclass(frozen=True)
class SetRef:
    """Reference to a set by stable id."""
    id: str

@dataclass(frozen=True)
class KeyframeRef:
    """Reference to a generated keyframe (Stage 1 still image)."""
    id: str

@dataclass(frozen=True)
class ClipRef:
    """Reference to a generated video clip (Stage 2 i2v output)."""
    id: str


# -- Scalar specs ------------------------------------------------------------

AspectRatio = Literal["9:16", "16:9", "1:1", "4:5"]
RenderStyle = Literal["photoreal", "stylized_3d_pixar_adjacent", "anime", "watercolor"]


@dataclass(frozen=True)
class Resolution:
    width: int
    height: int


@dataclass(frozen=True)
class VoiceConfig:
    """Provider-agnostic voice knobs. Each TTS adapter maps these to its own
    concepts. Kept loose (extra fields allowed) so adapters can carry
    provider-specific hints without a schema migration."""
    gender: str = ""
    pace: float = 1.0
    warmth: str = ""
    accent_hint: str = ""
    energy: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CameraSpec:
    """Per-scene camera intent. The animator agent interprets this through
    the template's motion vocabulary (panel = locked, song = dolly, etc.)."""
    motion: str = "static"            # static | dolly_in | dolly_out | ... (template extends)
    focus_on: CastMemberRef | None = None


@dataclass(frozen=True)
class CompositionSpec:
    """Where cast members sit in the frame. Template-specific subclasses can
    add e.g. `seating_arrangement` for a panel debate."""
    framing: str = "medium_close_up"  # ms | mcu | wide | ots | ... (template extends)
    cast_visible: tuple[CastMemberRef, ...] = ()


@dataclass(frozen=True)
class LightingSpec:
    """Locked lighting state for a Set. Templates with multiple lighting
    states (song video: golden hour vs. blue hour) instantiate one per state."""
    key_position: str = "camera_left"
    key_temperature: str = "neutral"
    contrast: str = "medium"


# -- Cast / Set protocols ----------------------------------------------------

@runtime_checkable
class CastMember(Protocol):
    """Anything with an identity, a character sheet, and a voice config.

    Templates subclass for genre specifics:
      - PanelDebateTemplate: Panelist(seat_position, role)
      - RomanticSongTemplate: Singer(voice_type, emotion_arc)
    """
    id: str
    name: str
    character_sheet: ImageRef
    voice: VoiceConfig


@runtime_checkable
class Cast(Protocol):
    """A group of cast members + a group anchor sheet (the never-degrading
    identity reference). Cardinality is template-decided."""
    members: list[CastMember]
    anchor_sheet: ImageRef


@runtime_checkable
class Set(Protocol):
    """A locked environment with master image + lighting state + style."""
    id: str
    master_image: ImageRef
    lighting: LightingSpec
    style: RenderStyle


@runtime_checkable
class SetLibrary(Protocol):
    """One or more sets. THE TABLOID has 1; a song video has 5–8."""
    sets: dict[str, Set]


# -- Scene IR ----------------------------------------------------------------
#
# The canonical structured representation of a scene. Every production agent
# produces or consumes this. Universal fields are first-class; genre-specific
# content goes in `payload` (e.g. PanelScenePayload, SongScenePayload).

P = TypeVar("P")


@dataclass
class Scene(Generic[P]):
    # Universal
    id: str
    show_id: str
    duration_seconds: int
    aspect_ratio: AspectRatio
    resolution: Resolution

    cast: list[CastMemberRef]
    set: SetRef

    # Endpoint-locked i2v: scene N's end_keyframe == scene N+1's start_keyframe.
    # Stage 1 generates these; Stage 2 animates between them.
    start_keyframe: KeyframeRef | None
    end_keyframe: KeyframeRef | None

    camera: CameraSpec
    composition: CompositionSpec

    # Genre-specific scene content. Templates fill this in (PanelScenePayload,
    # SongScenePayload, RecipeScenePayload). Base agents work over `Scene[P]`
    # generically; genre logic lives in template overrides.
    payload: P


# -- Stage 1 / Stage 2 artifacts --------------------------------------------

@dataclass
class ShotPlan:
    """Director output — the full per-scene plan that the executor runs.

    This is the contract between *deciding* what to render (director's job:
    pick speaker, motion, frame anchors, model choice, prompt) and
    *executing* it (executor's job: call Seedance with these exact args,
    persist the clip, prep the chain anchor for the next scene).

    Direct-mode pipelines short-circuit the director by reading
    pre-authored ShotPlans out of a script JSON. Production-mode
    pipelines have a Director agent generate ShotPlans dynamically. Both
    feed the same executor, so the downstream code path is identical.
    """
    scene_id: str
    scene_number: int | str          # int 1..N or string slug ("9b") for inserts
    speaker: CastMemberRef | None    # None = ensemble shot, no single focus

    # Frame anchors. The keyframe layer of the pipeline (Stage 1 in the
    # SDK doc) — these are the locked endpoints i2v interpolates between.
    # `start_image_url` is the i2v's `image_url`; `end_image_url` is the
    # i2v's `end_image_url` (None for free-landing scenes).
    start_image_url: str | None
    end_image_url: str | None = None

    # Verbatim director instruction in natural language ("zoom in on
    # arjun as he delivers the rebuttal"). Fed into the executor's
    # prompt-construction step.
    motion: str = "static"

    # Camera/composition intent (informational; executor uses it to
    # pick the camera_motion enum value Seedance accepts).
    framing: str = "medium_close_up"

    # The model + mode tag the executor should call. Direct-mode's
    # alternating-anchor pattern uses these tags:
    #   "i2v-avatar"  — scene 1; image_url=avatar, no end_image_url
    #   "i2v-locked"  — even scenes; image_url=prior last frame, end=avatar
    #   "i2v-chained" — odd scenes; image_url=prior last frame, no end
    #   "t2v"         — fallback when no avatar available
    model: str = "bytedance/seedance-2.0/fast/image-to-video"
    mode_tag: str = "i2v-avatar"

    # Pre-assembled prompt string, ready to feed Seedance. The director
    # composes this from cast visual_descriptions + speaker locator +
    # motion + spoken line + style stability suffix.
    prompt: str = ""

    # Native-audio spoken line (Seedance with generate_audio=true
    # delivers it lip-synced). Empty string for ensemble / silent shots.
    spoken_line: str = ""
    duration_seconds: int = 5
    aspect_ratio: AspectRatio = "9:16"
    seed: int = 0

    notes: str = ""


@dataclass
class Keyframe:
    """Stage 1 output: a still image at scene's start or end."""
    ref: KeyframeRef
    image: ImageRef
    scene_id: str
    position: Literal["start", "end"]


@dataclass
class Clip:
    """Stage 2 output: a video clip animating between two keyframes."""
    ref: ClipRef
    scene_id: str
    duration_seconds: float
    video_url: str | None
    local_path: str | None
    cost_usd: float = 0.0


@dataclass
class ContinuityReport:
    """Continuity agent output. Per-check pass/fail with details so a human
    or compiler can localize the regression."""
    scene_id: str
    checks: dict[str, bool]
    notes: dict[str, str] = field(default_factory=dict)
    passed: bool = True
