# Showrunner

**A production-pipeline-as-code framework for AI-generated, multi-scene shows.**

Showrunner is the infrastructure for rendering an entire show — locked cast, consistent style, scene-by-scene continuity — instead of one-off clips. The pipeline is universal; the genre is a template.

> **Status:** early. One working reference template ([The Tabloid](./the-tabloid/), an AI news debate channel) and the architecture below. Built for the BetaU Seed Agents Challenge finale (May 2026).

---

## Why this exists

Most "AI movie" tooling treats stills as a side effect of video generation. Showrunner inverts that:

1. **Frames are the source of truth. Video is the cheap interpolation between them.** Identity drift, style flipping, and continuity bugs become structurally catchable because they're caught at the still-image stage where iteration is cheap.
2. **Every agent is a pure function of `(inputs, asset_registry)`.** Re-running any agent with the same inputs produces the same output. Partial regeneration is safe.
3. **Typed artifacts between agents, not free text.** Free-text handoffs are how multi-agent systems become slow-motion telephone games. Every agent here emits a typed artifact (`ShotPlan`, `Keyframe`, `Clip`, `Scene[P]`); the types are the contract.
4. **Production pipeline first, writing pipeline second.** The expensive failures are in production (rendering), not writing. The SDK runs on hand-written scripts; the writing pipeline is bolted on as another producer of the same `Scene` artifact.
5. **The pipeline is universal; the genre is a template.** Research → structure → script → casting → set → director → storyboard → animator → continuity → editor applies to every show. What changes between a panel debate, a song video, and a cooking show is *what cast means, what set means, what a scene contains, and what continuity means.*

Full design rationale: [the-tabloid/MOVIE_SDK.md](./the-tabloid/MOVIE_SDK.md).

---

## Three-layer model

```
┌─────────────────────────────────────────────────┐
│ Layer 3: Show instances                         │
│   "THE TABLOID Episode 4: Skyroot launch"       │
│   "Couple at the beach — song video"            │
└─────────────────────────────────────────────────┘
                       ▲
┌─────────────────────────────────────────────────┐
│ Layer 2: Show templates (genre-specific)        │
│   PanelDebateTemplate (THE TABLOID lives here)  │
│   RomanticSongTemplate                          │
│   CookingShowTemplate                           │
└─────────────────────────────────────────────────┘
                       ▲
┌─────────────────────────────────────────────────┐
│ Layer 1: SDK core (genre-agnostic)              │
│   Pipeline, Asset Registry, Scene IR,           │
│   agent base classes, compiler interface        │
└─────────────────────────────────────────────────┘
```

- **Layer 1 — SDK core.** Knows nothing about any specific show. Defines the pipeline shape, the typed Scene IR with extension points, the asset registry pattern, and the agent base classes. Lives in [the-tabloid/backend/sdk/](./the-tabloid/backend/sdk/).
- **Layer 2 — Templates.** Opinionated configurations of Layer 1 for a genre. Subclass `CastMember`/`Cast`/`Set` and the `Scene.payload` generic; override agents with genre-specific behavior. The first reference template, `PanelDebateTemplate`, lives in [the-tabloid/backend/templates/panel_debate/](./the-tabloid/backend/templates/panel_debate/).
- **Layer 3 — Shows.** A specific cast, set, and slate of episodes built on a template. The Tabloid show data lives in [the-tabloid/backend/shows/the_tabloid/](./the-tabloid/backend/shows/the_tabloid/).

The Tabloid is *not* the SDK. The Tabloid is the first reference template plus a specific cast and set.

---

## The pipeline

Same shape for every template; what flows through it is genre-specific.

```
Premise + Goal
  │
[research_agent]                   ← grounded in truth (optional per template)
  ↓ research notes
[structure_agent]
  ↓ beat sheet
[script_agent]
  ↓ scenes with template-specific payload

[casting_agent] ──┐
[set_agent] ──────┤  run once per show; populate the asset registry
                  ↓
            asset_registry
                  ↓
[director_agent]                   ← per scene
  ↓ ShotPlan
[storyboard_agent]                 ← Stage 1: keyframes (still images)
  ↓ keyframes
[continuity_agent]                 ← verify before expensive operations
  ↓ approved keyframes
[animator_agent]                   ← Stage 2: i2v with locked endpoints
  ↓ clips
[editor_agent]
  ↓
Final episode
```

The Stage 1 / Stage 2 split is the core trick: agents lock the endpoints (start_keyframe, end_keyframe) as still images first, then animate *between* locked keyframes using image-to-video. Identity drift between scenes is structurally bounded because every scene's start frame is the previous scene's end frame.

---

## The reference template: The Tabloid

A 4-persona AI news debate channel (anchor / provocateur / analyst / humanist), rendered as a 6-scene cinematic short. PWA frontend, FastAPI + Celery backend, BytePlus Seed 2.0 + Seedance 2.0 + Seed Speech.

→ Full Tabloid docs: [the-tabloid/README.md](./the-tabloid/README.md) · [the-tabloid/SETUP.md](./the-tabloid/SETUP.md)

What the Tabloid template fills in:

| SDK abstraction | PanelDebateTemplate fills with |
| --- | --- |
| `CastMember` | `Panelist(seat_position, role)` |
| `Cast` | `PanelCast` — fixed 4 panelists, locked seating |
| `Set` | single `BroadcastStudio`, locked lighting |
| `Scene.payload` | `PanelScenePayload` — featured speaker, spoken lines, seating, beat type |
| Director grammar | medium close-ups, over-shoulder coverage |
| Animator motion | locked camera, subtle gestures, lip-synced VO |
| Continuity checks | `seat_consistency_check`, `wardrobe_consistency_check` |
| Editor grammar | hard cuts, lower thirds, sting beds |

---

## Building a new template

A template is a Python subclass package, not a YAML config (for now — see [Decision 1 in MOVIE_SDK.md](./the-tabloid/MOVIE_SDK.md)). To add a `RomanticSongTemplate` or `CookingShowTemplate`:

1. Subclass `CastMember` / `Cast` / `Set` with genre-specific fields
2. Define a `ScenePayload` type for what your scenes contain
3. Override the base agents whose grammar changes for your genre (typically: storyboard prompt composition, animator motion vocabulary, director shot-planning, continuity checks)
4. Optionally override writing-side agents (research/structure/script) if your genre has unusual writing needs

What you inherit for free:

- The asset registry (typed, versioned, queryable)
- Stage 1 / Stage 2 split with endpoint-locked i2v
- The "frames are truth" discipline
- Pure-function-of-`(inputs, registry)` re-render safety
- All agent base classes and the typed contracts between them

Building the second template is the real test of whether the abstractions generalize. Sketches for `RomanticSongTemplate` and `CookingShowTemplate` are in [MOVIE_SDK.md](./the-tabloid/MOVIE_SDK.md).

---

## Quick start

The reference template is the fastest way in. Mock mode runs the full pipeline locally with stubbed LLM/video/TTS.

```bash
cd the-tabloid
# follow the-tabloid/README.md for backend (FastAPI + Celery + redis)
# and frontend (Vite + React PWA)
```

Then open `http://localhost:5173` and pick a channel. Full setup with live BytePlus + Firebase keys: [the-tabloid/SETUP.md](./the-tabloid/SETUP.md).

---

## Repo layout

```
Showrunner/
├── README.md                          ← you are here
├── LICENSE                            ← Apache 2.0
└── the-tabloid/
    ├── MOVIE_SDK.md                   ← architecture + design decisions
    ├── README.md                      ← Tabloid (reference template) docs
    ├── SETUP.md                       ← live-API setup walkthrough
    ├── backend/
    │   ├── sdk/                       ← Layer 1: framework core
    │   │   ├── types.py               ← typed artifacts (Scene IR, refs, specs)
    │   │   ├── pipeline.py            ← pipeline shape
    │   │   ├── registry.py            ← asset registry
    │   │   ├── agents/base.py         ← agent base classes
    │   │   └── providers/             ← model adapters (BytePlus, Fal, ffmpeg)
    │   ├── templates/                 ← Layer 2: genre templates
    │   │   └── panel_debate/          ← reference template
    │   │       ├── cast.py · set.py · scene.py
    │   │       ├── agents/            ← genre overrides
    │   │       └── pipelines/         ← full / sample / direct modes
    │   └── shows/                     ← Layer 3: show data
    │       └── the_tabloid/           ← reference show
    │           ├── anchors.py
    │           ├── personas.py
    │           └── set.py
    └── frontend/                      ← Tabloid PWA (Vite + React)
```

---

## License

[Apache License 2.0](./LICENSE).
