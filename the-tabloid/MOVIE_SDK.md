# Movie SDK — Architecture

A production-pipeline-as-code SDK for generating multi-scene shows with locked cast and consistent style. Built around the learnings from THE TABLOID's Seedance 2.0 pipeline, but designed to host any genre of show as a template on top of the same core.

## Design philosophy

**Frames are the source of truth. Video is the cheap interpolation between them.**

Most "AI movie" tooling treats stills as a side effect of video generation. This SDK inverts that: the production pipeline generates and verifies still keyframes first, then animates *between* locked keyframes. Identity drift, style flipping, and continuity bugs become structurally impossible (or at least, structurally catchable) because they're caught at the still-image stage where iteration is cheap.

**Every agent is a pure function of (inputs, asset_registry).**

No hidden state. Re-running any agent with the same inputs produces the same output. This is what makes partial regeneration safe — you can re-render scene 5 without touching scenes 1–4, because each scene is an independent computation over a versioned asset registry.

**Typed artifacts between agents, not free text.**

Free-text handoffs between agents are how multi-agent systems become slow-motion telephone games. Every agent in this SDK emits a typed artifact (`ShotPlan`, `Keyframe`, `Clip`, etc.) and consumes typed artifacts from upstream. The types are the contract.

**Production pipeline first, writing pipeline second.**

The expensive failure modes are in production (rendering), not writing. The SDK is designed so the production pipeline can run on hand-written scripts, and the writing pipeline can be bolted on later as another producer of the same `Scene` artifact.

**The pipeline is universal; the genre is a template.**

The pipeline (research → structure → script → casting → set → director → storyboard → animator → continuity → editor) applies to every show. What changes between a panel debate, a romantic song, and a cooking show is *what cast means, what set means, what a scene contains, and what continuity means.* The SDK ships the pipeline; templates fill in the genre.

---

## Three-layer model

The SDK is structured as three layers. Each layer extends the one below it.

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

**Layer 1** knows nothing about THE TABLOID, panel debates, or any specific show. It defines the pipeline shape, the asset registry pattern, the Scene IR with extension points, and the agent base classes. It's the part that doesn't change.

**Layer 2** is opinionated configurations of Layer 1 for a specific genre. Each template subclasses the base abstractions and fills in genre-specific details: what cast means, what scenes look like, what continuity checks apply, what visual grammar the director and animator use.

**Layer 3** is a specific show built on a template — a particular cast, a particular set, a particular episode of content.

THE TABLOID is *not* the SDK. THE TABLOID is the first reference template (`PanelDebateTemplate`) plus a specific cast and set.

---

## Core abstractions (Layer 1)

Each abstraction has a stable core (universal across shows) and explicit extension points (where templates customize).

### Asset Registry

A typed, queryable index of every asset in the show. Not just files — files plus metadata: what they represent, what version they are, what other assets they're compatible with.

```
asset_registry:
  cast_anchor_sheet_v3.png:
    type: cast_anchor
    show: the_tabloid
    members: [amit, arjun, veera, anjali]
    style: stylized_3d_pixar_adjacent
    compatible_with: [set_master_v2, kf_*_v3]

  veera_sheet_v2.png:
    type: character_sheet
    member: veera
    style: stylized_3d_pixar_adjacent
    derives_from: cast_anchor_sheet_v3
```

Every other agent queries the registry by typed reference (`CastMemberRef("veera")`), never by filename. When an asset is regenerated, its version bumps, and downstream artifacts that depend on the old version are flagged stale.

**Universal, no extension points needed.** Templates use the same registry; only the *types* of assets registered differ.

### Cast (extensible)

The base SDK defines `CastMember` as a protocol — anything with an identity, a character sheet, and a voice config. Templates subclass it for their genre.

```python
class CastMember(Protocol):
    id: str
    character_sheet: ImageRef
    voice_config: VoiceConfig

class Cast(Protocol):
    members: List[CastMember]
    anchor_sheet: ImageRef    # the group reference image
```

**Extension point.** Each template defines its own `CastMember` and `Cast` subclasses:

- `PanelDebateTemplate`: `Panelist(seat_position, role)` + `PanelCast(fixed 4 seats)`
- `RomanticSongTemplate`: `Singer(voice_type, emotion_arc)` + `DuoCast(2 leads)`
- `CookingShowTemplate`: `Host` + `GuestChef` + `CookingCast(variable)`

### Set (extensible)

The base abstraction is a `SetLibrary` containing `Set` instances. THE TABLOID's library has one entry; a romantic song's library has 5–8.

```python
class Set(Protocol):
    id: str
    master_image: ImageRef
    lighting_state: LightingSpec
    style: RenderStyle

class SetLibrary(Protocol):
    sets: Dict[str, Set]
```

**Extension point.** Templates decide cardinality and what counts as "lighting state" or "style." A panel debate has one locked set with fixed lighting; a song video has many locations with golden-hour vs. nighttime vs. interior states.

### Scene IR (extensible via payload)

The canonical structured representation of a scene. Every production agent produces or consumes this artifact.

```python
@dataclass
class Scene(Generic[P]):
    # Universal fields (every show has these)
    id: str
    show_id: str
    duration_seconds: int
    aspect_ratio: AspectRatio
    resolution: Resolution

    cast: List[CastMemberRef]       # who appears in this scene
    set: SetRef                     # where it happens

    start_keyframe: KeyframeRef
    end_keyframe: KeyframeRef       # shared with next scene's start

    camera: CameraSpec
    composition: CompositionSpec

    # Extension point — genre-specific scene content
    payload: P
```

**Extension point.** Each template defines its own payload type:

```python
class PanelScenePayload:               # PanelDebateTemplate
    featured_speaker: PanelistRef
    spoken_lines: List[SpokenLine]
    seating: SeatingArrangement

class SongScenePayload:                 # RomanticSongTemplate
    lyrics: LyricSegment
    emotion: Emotion
    cast_present: List[SingerRef]

class RecipeScenePayload:               # CookingShowTemplate
    step_number: int
    technique: Technique
    ingredients_used: List[IngredientRef]
```

The base agents work on `Scene[P]` generically. Genre-specific logic lives in template overrides.

---

## Agents (Layer 1 base classes, Layer 2 overrides)

Each agent is a node in the production graph. The base class defines the I/O contract; templates override behavior with genre-specific logic.

### Research agent
**In:** premise, goal
**Out:** research notes
**Job:** ground the show in truth before scripting.
**Override surface:** what counts as "research" — a panel show wants news sources; a song video might not need a research step at all.

### Structure agent
**In:** premise, research notes
**Out:** beat sheet (acts, scenes, episode skeleton)
**Job:** decide the shape of the show.
**Override surface:** structural grammar. Panel debate uses three-act news segments; song video uses verse/chorus/bridge; cooking show uses prep → cook → plate.

### Script agent
**In:** beat sheet, research notes
**Out:** scenes with content payload populated
**Job:** write the actual content.
**Override surface:** what "writing" produces — dialogue + spoken lines for panel; lyrics + emotion beats for song; instructions + technique notes for cooking.

### Casting agent
**In:** show concept, character bibles
**Out:** cast in asset registry (anchor sheet + per-character sheets)
**Job:** build the never-degrading identity reference layer.
**Override surface:** what cast members exist and how they're rendered. Run once per show.

### Set agent
**In:** show concept
**Out:** set master image(s) in asset registry
**Job:** establish locked environments.
**Override surface:** how many sets, what they look like, what lighting states each supports. Run once per show.

### Director agent
**In:** scene (resolved), asset registry
**Out:** ShotPlan
**Job:** creative direction at the scene level.
**Override surface:** visual grammar. Panel director thinks in over-shoulder cuts and seat coverage; song director thinks in two-shots and emotional close-ups; cooking director thinks in overhead inserts and reaction shots.

### Storyboard agent (Stage 1)
**In:** ShotPlan, asset registry
**Out:** Keyframes
**Job:** turn the plan into actual pixels via still-image model.
**Override surface:** the prompt composition. Templates fill in genre-specific language for what the keyframe should show.

### Animator agent (Stage 2)
**In:** keyframe pair, motion spec
**Out:** Video clip
**Job:** animate between two locked keyframes via i2v with `image_url` + `end_image_url`.
**Override surface:** motion vocabulary. Panel = locked camera + small gestures; song = slow dolly + emotion-driven push-ins; cooking = overhead-to-eye-level cuts. Stability suffix is universal.

### Continuity agent
**In:** asset registry, generated keyframes/clips
**Out:** continuity report
**Job:** verify consistency.
**Override surface:** which checks apply. Panel runs `seat_consistency_check`, `wardrobe_consistency_check`. Song runs `couple_identity_check`, `emotional_arc_consistency`. Cooking runs `ingredient_state_check` across steps.

### Editor agent
**In:** ordered clips, audio tracks, overlays
**Out:** final video
**Job:** assemble clips into final cut.
**Override surface:** transitions and timeline grammar. Panel = hard cuts + lower thirds. Song = beat-synced cuts + music underscore. Cooking = recipe-step graphics + ingredient callouts.

The interface is a *timeline*, not a concat list — designed so future capabilities (music, lower thirds, B-roll, trimming) slot in without rewriting the agent.

---

## The full pipeline

Same pipeline shape for every template. What flows through it is genre-specific.

```
Premise + Goal
  │
[research_agent]                   ← grounded in truth (optional per template)
  ↓ (research notes)
[structure_agent]
  ↓ (beat sheet)
[script_agent]
  ↓ (scenes with template-specific payload)

[casting_agent] ───┐
[set_agent] ───────┤  (run once per show; build asset registry)
                   ↓
              asset_registry
                  ↓
[director_agent]                   ← per scene
  ↓ (ShotPlan)
[storyboard_agent]                 ← Stage 1: keyframes
  ↓ (keyframes)
[continuity_agent]                 ← verify before expensive operations
  ↓ (approved keyframes)
[animator_agent]                   ← Stage 2: i2v with locked endpoints
  ↓ (clips)
[editor_agent]
  ↓
Final episode
```

---

## Templates (Layer 2)

A template is an opinionated configuration of the SDK core for a specific genre. Building a template means:

1. Subclass `CastMember` and `Cast` with genre-specific fields
2. Define the `Set` cardinality (single locked set, or multiple locations)
3. Define a `ScenePayload` type for the genre's scene content
4. Override the base agents with genre-specific behavior:
   - Storyboard prompt composition (genre-specific language)
   - Animator motion vocabulary (locked vs. dolly vs. handheld)
   - Director shot-planning logic
   - Continuity check selection
5. Optionally override writing-side agents (research/structure/script) if the genre has unusual writing needs

What a template inherits for free from the SDK core:

- The asset registry pattern (typed, versioned, queryable)
- The Stage 1 / Stage 2 split (keyframes first, animate between locked endpoints)
- The endpoint-locked i2v pattern (`image_url` + `end_image_url`)
- The "frames are truth, video is interpolation" philosophy
- The pure-function-of-(inputs, registry) discipline
- The compile-before-render gating (when scene_compiler exists)
- The build order discipline (production pipeline before writing pipeline)
- The stability suffix and standard render constraints
- All agent base classes and the typed contracts between them

This is the leverage. Building a song video from scratch on raw Seedance is weeks of trial and error. Building it as a template on top of the SDK should be days, because all the consistency-and-drift architecture is already in place.

### Reference template: PanelDebateTemplate (THE TABLOID)

The first reference template, and the one that drove the SDK's design.

- **CastMember:** `Panelist(seat_position: int, role: Literal["provocateur", "analyst", "anchor", "humanist"])`
- **Cast:** `PanelCast` — fixed 4 panelists with locked seating
- **Set:** single `BroadcastStudio` with locked lighting
- **ScenePayload:** `PanelScenePayload` — featured speaker, spoken lines, seating, beat type
- **Director:** picks featured speaker per scene, plans medium close-ups with over-shoulder coverage of remaining panelists
- **Animator:** locked camera, subtle gestures, lip-synced VO
- **Continuity:** `seat_consistency_check`, `wardrobe_consistency_check`
- **Editor:** hard cuts, lower thirds, optional sting beds

### Sketch: RomanticSongTemplate

To illustrate what a second template would override.

- **CastMember:** `Singer(voice_type: Literal["lead", "harmony"], emotion_arc: List[Emotion])`
- **Cast:** `DuoCast` — two leads, defined relationship
- **Set:** `LocationLibrary` with multiple entries (cafe, beach, train station, bedroom) each with multiple lighting states (golden hour, blue hour, interior)
- **ScenePayload:** `SongScenePayload` — lyric segment, emotion, cast present, location, lighting state
- **Director:** picks intimate two-shots, emotion-driven framing, plans push-ins on emotional peaks
- **Animator:** slow dollies, soft handheld, push-in on emotion peak — vs. THE TABLOID's locked camera
- **Continuity:** `couple_identity_check` across locations, `emotional_arc_consistency` across the song
- **Editor:** beat-synced cuts to the song's rhythm, music underscore as primary audio bed
- **Writing-side override:** `script_agent` produces lyric segments aligned to song structure, not dialogue

### Sketch: CookingShowTemplate

- **CastMember:** `Host` + `GuestChef`
- **Cast:** `CookingCast` — variable, host always present
- **Set:** single `KitchenStudio` with multiple camera positions (overhead, eye-level, close-up of ingredients)
- **ScenePayload:** `RecipeScenePayload` — step number, technique, ingredients used at this step
- **Director:** alternates overhead inserts with eye-level reaction shots
- **Continuity:** `ingredient_state_check` — what's on the counter at scene N must match what was added through scenes 1..N-1
- **Editor:** recipe-step graphics, ingredient callouts, time-elapsed overlays

---

## Build order

Build the SDK and the first template (PanelDebateTemplate / THE TABLOID) together. The order:

1. **SDK core: asset registry + base abstractions** (Cast, Set, Scene IR with payload generic)
2. **Template: PanelDebateTemplate skeleton** (Panelist, PanelCast, PanelScenePayload, BroadcastStudio)
3. **SDK + template: storyboard agent + animator agent** — the Stage 1 / Stage 2 split that already works
4. **SDK + template: director agent**
5. **SDK + template: continuity agent**
6. **SDK + template: script agent + structure agent** (writing pipeline)
7. **SDK + template: editor agent** — last, ffmpeg concat is fine for now
8. **SDK: research agent** — last on writing side, hand-written scripts are fine for early shows
9. **Second template (RomanticSongTemplate or other)** — once the first template is solid, build a second to validate that the abstractions actually generalize

Building the second template is the real test of whether the SDK is a framework or just THE TABLOID with extra steps. If the second template requires reaching into Layer 1 to change things, the abstractions need work. If it doesn't, the SDK is real.

---

## Two architectural decisions to make explicit

### Decision 1: Templates are code, not config (for now)

Templates are Python subclasses, not YAML configurations. Code gives full expressiveness — overrides can be arbitrary functions, not just parameter substitutions.

Once 3–4 templates exist and the patterns are visible, a config layer can be introduced for the 80% case (declarative templates for users who don't need code overrides) while keeping code-as-template available for advanced users. Going code-first makes this evolution natural; going config-first forces an awkward escape hatch later.

### Decision 2: The SDK is opinionated, the template chooses what to skip

The pipeline shape is genuinely correct for almost every show. Don't make template authors decide whether to have a continuity agent. *Ship the continuity agent; let templates decide which checks it runs.*

The exception is the writing pipeline. Some shows have hand-written scripts. Some have generated scripts. The SDK lets templates skip writing agents and feed scenes in directly — the production pipeline runs on Scene objects regardless of where they came from.

---

## Future work: the Scene Compiler

**Status: not built. Compilation is currently done manually.**

The single most important architectural piece this SDK is missing. Worth building when manual compilation starts to bottleneck.

### Why it matters

Right now, agents loosely produce artifacts and we hope they fit together. The failure mode is the slow-motion telephone game: every agent does its job correctly, and the output is still wrong because the agents disagreed on context that wasn't passed explicitly.

A compiler reframes this. The canonical Scene IR is the source language; every step of the pipeline is either *lowering* it toward video or *checking* that the lowering is valid. Bugs that today surface after $24 of video credits would surface as compile errors before any image model is even called.

### What it does

Five passes, each one cheap, each one catching a different class of bug:

**Pass 1 — Resolution.** Resolve every named reference in the Scene against the asset registry. Fails if any reference is unresolvable (e.g. a cast member with no character sheet).

**Pass 2 — Type checking.** Verify resolved references are compatible:
- Cast sheet style must equal set master style
- Character bible's wardrobe must match what's actually rendered in the character sheet
- Featured cast member must be in the scene's cast
- Spoken line / lyric language matches voice config language

This is where THE TABLOID's "bible says navy bandhgala but sheet shows grey suit" bug would have been caught.

**Pass 3 — Constraint checking.** Verify the scene is physically renderable:
- Spoken line / lyric word count fits within `duration` at natural pace
- Endpoint frames are reachable in `duration` seconds
- Aspect ratio of references matches output
- Motion budget is sane

**Pass 4 — Lowering.** Emit deterministic API call payloads (Stage 1 keyframe prompts, Stage 2 i2v call). Pure templating, no LLM creativity.

**Pass 5 — Continuity verification (post-render, pre-video).** After keyframes are generated but before video is generated:
- Visual diff: scene N's end keyframe vs scene N+1's start keyframe (should be byte-identical)
- Style classifier: did keyframes drift from master style?
- Identity check: are the right cast members present?

If this fails, regenerate keyframes (cheap), not videos (expensive).

### Compiler-as-protocol

The compiler isn't a separate phase between agents — it's the *protocol* by which agents talk to each other. Every artifact passes through the relevant compile passes before the next agent consumes it. Each layer of expensive work is gated by a layer of cheap verification.

### Templates extend compile passes

Compile passes are themselves an extension point. The base SDK ships universal passes (reference resolution, asset compatibility, duration sanity). Templates register additional passes specific to their genre:

- `PanelDebateTemplate`: `seat_consistency_pass`, `wardrobe_lock_pass`
- `RomanticSongTemplate`: `couple_identity_pass`, `lyric_timing_pass`
- `CookingShowTemplate`: `ingredient_state_pass`

This is symmetric to how the continuity agent works — base shape, template-specific checks.

### What it gets you

| Property | Without compiler | With compiler |
|---|---|---|
| Error surface | After expensive renders | Before any render runs |
| Debuggability | Reason backwards through agent choices | Typed, localized errors |
| Partial regeneration | Manual stale-tracking, error-prone | Automatic via version bumps |
| Trust | "Hope it works" | "If it compiles, the structure is sound" |

### The trap to avoid

Don't make the compiler "smart." Resist auto-fixing. Compilers fail loudly with clear errors; they don't make creative decisions on behalf of upstream agents.

The exception is deterministic lowering (Pass 4) — that's mechanical translation, not auto-fixing.

### Two levels, eventually

- **`scene_compiler`** — verifies and lowers a single scene. Build first.
- **`episode_compiler`** — verifies cross-scene properties (continuity chain, total runtime, asset version coherence). Build when episode-level bugs start mattering.

---

## What we're doing today (manual compilation)

Until the scene_compiler is built, the human is the compiler. That works for low scene counts and a single template, but the failure modes to watch for:

- **Asset/bible mismatches** — verify by hand that character bibles match what's actually in the cast anchor sheet (the THE TABLOID failure mode)
- **Style consistency** — verify by hand that every reference image is in the same render style
- **Endpoint reachability** — sanity-check that scene N's end frame and scene N+1's start frame are the same file
- **Spoken line / lyric length** — sanity-check that VO fits in duration at natural pace
- **Stale references** — when an asset is regenerated, manually track which scenes need their keyframes redone

Each of these is a future compile pass. Catching them manually now is tractable. The signal that it's time to build the compiler is when manual checking starts missing things, when partial regeneration becomes painful, or when the second template ships and cross-template invariants need enforcing.

---

## Framing

The real abstraction is **production pipeline as code** — closer to Terraform-for-video than to a movie-making app. The SDK is the language a show is described in; the agents are the runtime that produces it; templates are the shows' genre dialects.

What this framing tells you to optimize for: reproducibility, partial regeneration, typed handoffs, explicit asset versioning, template extensibility. Not "make pretty videos easier." Pretty videos easier is the surface; the moat is *render this same show correctly with one cast member replaced six episodes in without rerendering everything, and have someone else build a song video on top of the same foundation in days.*
