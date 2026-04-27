"""Movie SDK — Layer 1.

Genre-agnostic core. Knows nothing about THE TABLOID, panel debates, or any
specific show. Defines the pipeline shape, the asset registry, the Scene IR
with extension points, and the agent base classes.

See MOVIE_SDK.md (repo root) for the full architecture writeup.

Layer 2 (templates/) extends this for a specific genre.
Layer 3 (shows/) is a specific show built on a template.
"""
from __future__ import annotations
