"""THE TABLOID — show instance built on the PanelDebateTemplate.

Layer 3. Brings together:
  - the genre template (panel_debate)
  - this show's specific cast (the seven channel anchors in `anchors.py`
    plus the fallback persona pools in `personas.py`)
  - this show's specific set (the locked broadcast studio with THE
    TABLOID branding, defined in `set.py`)

A second show on the same template (e.g. a Tamil-language local-politics
panel debate) would live alongside this one with its own cast + set
files and re-use everything in `templates/panel_debate/`.
"""
from __future__ import annotations
