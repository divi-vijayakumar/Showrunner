"""Per-mode pipelines for the panel-debate template.

Three production modes, all on the same template:

  full     — production: RSS → research → debate → script → video.
  direct   — short-circuited: hand-authored script JSON drives the
             execution + edit + render path. Same downstream code as
             full mode; the upstream agents (story/casting/script/
             director) are replaced by a script-JSON reader so demos
             are fast, deterministic, and don't burn LLM credits.
  sample   — single 5s anchor teaser. Skips the multi-scene loop.

This module re-exports the public surface so callers (main.py, the
celery task) don't have to know which file each function lives in.
"""
from __future__ import annotations

from ._helpers import (
    SEGMENTS_DIR,
    load_segment_manifest,
)
from .direct import _generate_direct
from .full import _generate, celery_app, generate_segment
from .sample import _generate_sample

__all__ = [
    "SEGMENTS_DIR",
    "load_segment_manifest",
    "_generate",
    "_generate_direct",
    "_generate_sample",
    "celery_app",
    "generate_segment",
]
