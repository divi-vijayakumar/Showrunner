"""THE TABLOID's locked broadcast studio.

The single set instance the show always renders into. Same desk, same
backlight, same lighting state across every channel and every episode.
Channel-specific visual styling (e.g. the warm-rose wash on the
celebrity channel) is layered into the set dressing prompt, not into
this Set instance.
"""
from __future__ import annotations

from ...sdk.types import ImageRef, LightingSpec
from ...templates.panel_debate.set import BroadcastStudio, PanelDebateSetLibrary


THE_TABLOID_STUDIO = BroadcastStudio(
    id="the_tabloid_studio",
    # Local avatar PNG used as the master stage frame for direct mode;
    # the pipeline uploads it to Fal storage on first run and caches the
    # public URL in a sidecar (see _resolve_local_avatar).
    master_image=ImageRef("scripts/The_tabloid_set.png"),
    lighting=LightingSpec(
        key_position="camera_left",
        key_temperature="cool_neutral",
        contrast="medium_high",
    ),
    style="stylized_3d_pixar_adjacent",
    fixtures=(
        "curved anchor desk centered in frame",
        "back-wall glowing letters reading exactly 'THE TABLOID'",
        "out-of-focus broadcast monitors behind the desk",
        "dark studio background, modern glass-and-metal",
    ),
)


SET_LIBRARY = PanelDebateSetLibrary(studio=THE_TABLOID_STUDIO)
