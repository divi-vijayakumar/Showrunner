# Scene 2 — Exact Payload to Fal Seedance r2v

Segment: `seg_291a1d4f6b` · Script: `skyroot` · Scene: `2` · Mode: `r2v-dual`

## Endpoint
`bytedance/seedance-2.0/fast/reference-to-video`

## Request body fields
| Field | Value |
|---|---|
| `duration` | `10` (Seedance accepts 4-15; we requested 10) |
| `aspect_ratio` | `9:16` |
| `resolution` | `720p` |
| `generate_audio` | `true` |
| `seed` | `1253160364` |

## `image_urls` (passed verbatim to the API)

### `image_urls[0]` → `@Image1`
- Role: GROUND TRUTH (faces, framing, style)
- URL: <https://v3b.fal.media/files/b/0a97e2d1/m2CaFChQrVQ3dnzmvuLIu_chain_anchor_after_scene_01.png>
- Preview:
  ![chain anchor](https://v3b.fal.media/files/b/0a97e2d1/m2CaFChQrVQ3dnzmvuLIu_chain_anchor_after_scene_01.png)

### `image_urls[1]` → `@Image2`
- Role: SETUP REFERENCE ONLY (desk, backdrop, lighting, seat order)
- URL: <https://v3b.fal.media/files/b/0a97e2c4/GfQY0ZE9HbLcmtCx8EJ4P_The_Tabloid_set.png>
- Preview:
  ![avatar](https://v3b.fal.media/files/b/0a97e2c4/GfQY0ZE9HbLcmtCx8EJ4P_The_Tabloid_set.png)

## `prompt` (full text sent to Fal)

```
LOCKED SET — modern broadcast news-debate studio of THE TABLOID. Curved anchor desk centered. Back wall: large glowing letters reading exactly 'THE TABLOID' (T-H-E space T-A-B-L-O-I-D). Out-of-focus broadcast monitors behind the desk. Strong key light from camera-left, soft fill from camera-right, dark studio background. NOT a library. NOT a study. NO bookshelves. NO wood paneling. Modern glass-and-metal news set. The set is IDENTICAL in every scene of this episode.

LOCKED CAST — EXACTLY FOUR PANELISTS, NOT THREE, NOT FIVE. Same four named people in every scene, in their fixed seating order at the desk:
  · SEAT 1 (far left): Amit Sharma (provocateur). South Asian man, AGE 53 specifically (NOT 60+, NOT elderly, NOT senior, NOT silver-haired). Salt-and-pepper hair, 65% still-black with grey at the temples, brushed back from a high forehead — full head of hair, NOT thinning. Smooth cheeks with only a slight forehead crease when frowning. Dark navy bandhgala-style buttoned jacket over a cream collarless shirt. Reading glasses tucked into chest pocket. Expressive eyes, slight permanent furrow. Strong jawline. No smile in resting frame.
  · SEAT 2 (center-left): Arjun Rao (analyst). South Asian man, AGE 44 specifically (NOT 50+, neither weathered nor youthful — mid-40s exactly). Slim build, thin wire-frame glasses. Charcoal-grey single-breasted blazer over a pale blue oxford shirt, no tie. Salt-and-pepper close-cropped hair, 70% black with light grey at the sides. Smooth professional skin, light forehead lines only. Intelligent direct gaze. Hands often folded on the desk. Composed, no theatrical gestures.
  · SEAT 3 (center-right): Veera Naatchi (anchor). Modern Indian woman, AGE 38 specifically (NOT older, NOT 40+, NOT mid-40s). Sharp shoulder-length straight black hair with a confident side part — fully black, no grey. Smooth youthful skin, no visible aging marks, no crow's feet. One statement gold ear-cuff, otherwise minimal jewelry. Tailored deep-burgundy structured blazer over a crisp white shell. Bold lip color. Composed, slightly forward-leaning posture. Direct, intelligent gaze. No smile in resting frame — strategic, not theatrical.
  · SEAT 4 (far right): Anjali Mehta (humanist). South Asian woman, AGE 37 specifically (NOT 40+, NOT mid-40s — late 30s exactly). Shoulder-length wavy black hair, side parting — fully black, no grey. Warm rounded cheeks, no harsh lines, only soft smile lines when smiling. Warm-toned forest-green silk blouse with a small gold pendant necklace. Subtle natural makeup, soft lip. Open warm expression, often a slight smile. Eyes that meet the camera with empathy. Hands often gesturing softly.

DO NOT add any additional panelists. DO NOT replace any panelist. DO NOT introduce guest commentators or background people. The four panelists named above are the ONLY people on this set, ever.

FEATURED SPEAKER THIS SCENE: Veera Naatchi (sitting at center-right of the desk). Camera focuses on Veera Naatchi; the other three panelists remain seated and visible at the desk in the surrounding frame (over-shoulder).

WARDROBE LOCK — every panelist wears the EXACT outfit, hair, jewelry, and makeup described above in every scene. Same person, same clothes, same lighting. The set, cast, and lighting are invariants.

CONTINUITY LOCK (across every scene of this episode):
- Every panelist has the IDENTICAL face, IDENTICAL age, IDENTICAL hair (same color, same length, same style) in every scene. NO aging between scenes.
- NO panelist gains gray hair. NO panelist gains wrinkles. NO panelist develops a beard or new facial hair. NO panelist appears older or younger than their stated age.
- The featured speaker has the same face every scene they appear. The three background panelists (not currently speaking) have the same face every time they're visible at the edge of frame — DO NOT redraw them as different people.

STYLE: Broadcast-polished, composed. The Daily Show / The Tabloid grammar — confident, locked, no kinetic camera tricks.

BRAND LOCK — backdrop text reads 'THE TABLOID' (capital letters, exactly that spelling).

THIS SCENE: PANEL DISCUSSION (no camera-direct address). Veera medium close-up, head turned slightly toward Amit at her left, hand sweeping across the panel as she names the story. Scans from Amit to Anjali on 'three views'. Authoritative measured cadence.

IMAGE REFERENCES (priority: @Image1 dominates identity, @Image2 is setup-only):
@Image1 is the GROUND TRUTH for everything in this scene — use it for camera framing, set composition, lighting, panelist faces, panelist ages, panelist wardrobes, panelist hair, panelist seat positions, and the overall photoreal rendering style. Every panelist visible in this shot must match @Image1 EXACTLY in face, age, hair, and wardrobe.

@Image2 is a SETUP REFERENCE ONLY — use it ONLY to confirm the desk shape, the THE TABLOID backdrop, the studio lighting design, and which panelist sits in which seat position (left-to-right order). DO NOT use @Image2 for faces. DO NOT use @Image2 for the rendering style. DO NOT blend @Image2's facial features into the panelists. DO NOT change anyone's seating position from what is in @Image1. If @Image1 and @Image2 disagree on visual style, ALWAYS follow @Image1.

DO NOT make this scene more cartoon-stylized than @Image1. Keep the photorealistic broadcast look established by @Image1.

AUDIO: Studio dialogue only. The speaker's voice over clean room tone. NO music, NO sound effects, NO dramatic stings, NO whip-pan whooshes, NO gunshots, NO explosions, NO breaking glass, NO action-movie ambient. Treat this as a calm news-debate set.

SPOKEN LINE — DELIVER VERBATIM. The featured speaker speaks this line in full within the 5-second clip. DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add words. DO NOT summarize. Lip-sync the speaker's mouth to these exact words at a natural broadcast pace.
Veera Naatchi: "Skyroot just flagged Vikram One off, to Sri Hari Kota. Telangana's Chief Minister attended. Launch window, is June. Three views, one rocket." Camera motion: locked, no camera movement — held frame. Held frame from start to end. Do not re-light or re-stage. Aspect ratio: 9:16. Broadcast-polished composition.
```

## Per-section breakdown

### 1. Panel lock (always present)
```
LOCKED SET — modern broadcast news-debate studio of THE TABLOID. Curved anchor desk centered. Back wall: large glowing letters reading exactly 'THE TABLOID' (T-H-E space T-A-B-L-O-I-D). Out-of-focus broadcast monitors behind the desk. Strong key light from camera-left, soft fill from camera-right, dark studio background. NOT a library. NOT a study. NO bookshelves. NO wood paneling. Modern glass-and-metal news set. The set is IDENTICAL in every scene of this episode.

LOCKED CAST — EXACTLY FOUR PANELISTS, NOT THREE, NOT FIVE. Same four named people in every scene, in their fixed seating order at the desk:
  · SEAT 1 (far left): Amit Sharma (provocateur). South Asian man, AGE 53 specifically (NOT 60+, NOT elderly, NOT senior, NOT silver-haired). Salt-and-pepper hair, 65% still-black with grey at the temples, brushed back from a high forehead — full head of hair, NOT thinning. Smooth cheeks with only a slight forehead crease when frowning. Dark navy bandhgala-style buttoned jacket over a cream collarless shirt. Reading glasses tucked into chest pocket. Expressive eyes, slight permanent furrow. Strong jawline. No smile in resting frame.
  · SEAT 2 (center-left): Arjun Rao (analyst). South Asian man, AGE 44 specifically (NOT 50+, neither weathered nor youthful — mid-40s exactly). Slim build, thin wire-frame glasses. Charcoal-grey single-breasted blazer over a pale blue oxford shirt, no tie. Salt-and-pepper close-cropped hair, 70% black with light grey at the sides. Smooth professional skin, light forehead lines only. Intelligent direct gaze. Hands often folded on the desk. Composed, no theatrical gestures.
  · SEAT 3 (center-right): Veera Naatchi (anchor). Modern Indian woman, AGE 38 specifically (NOT older, NOT 40+, NOT mid-40s). Sharp shoulder-length straight black hair with a confident side part — fully black, no grey. Smooth youthful skin, no visible aging marks, no crow's feet. One statement gold ear-cuff, otherwise minimal jewelry. Tailored deep-burgundy structured blazer over a crisp white shell. Bold lip color. Composed, slightly forward-leaning posture. Direct, intelligent gaze. No smile in resting frame — strategic, not theatrical.
  · SEAT 4 (far right): Anjali Mehta (humanist). South Asian woman, AGE 37 specifically (NOT 40+, NOT mid-40s — late 30s exactly). Shoulder-length wavy black hair, side parting — fully black, no grey. Warm rounded cheeks, no harsh lines, only soft smile lines when smiling. Warm-toned forest-green silk blouse with a small gold pendant necklace. Subtle natural makeup, soft lip. Open warm expression, often a slight smile. Eyes that meet the camera with empathy. Hands often gesturing softly.

DO NOT add any additional panelists. DO NOT replace any panelist. DO NOT introduce guest commentators or background people. The four panelists named above are the ONLY people on this set, ever.

FEATURED SPEAKER THIS SCENE: Veera Naatchi (sitting at center-right of the desk). Camera focuses on Veera Naatchi; the other three panelists remain seated and visible at the desk in the surrounding frame (over-shoulder).

WARDROBE LOCK — every panelist wears the EXACT outfit, hair, jewelry, and makeup described above in every scene. Same person, same clothes, same lighting. The set, cast, and lighting are invariants.

CONTINUITY LOCK (across every scene of this episode):
- Every panelist has the IDENTICAL face, IDENTICAL age, IDENTICAL hair (same color, same length, same style) in every scene. NO aging between scenes.
- NO panelist gains gray hair. NO panelist gains wrinkles. NO panelist develops a beard or new facial hair. NO panelist appears older or younger than their stated age.
- The featured speaker has the same face every scene they appear. The three background panelists (not currently speaking) have the same face every time they're visible at the edge of frame — DO NOT redraw them as different people.

STYLE: Broadcast-polished, composed. The Daily Show / The Tabloid grammar — confident, locked, no kinetic camera tricks.

BRAND LOCK — backdrop text reads 'THE TABLOID' (capital letters, exactly that spelling).
```

### 2. THIS SCENE: (verbatim from skyroot.json)
```
PANEL DISCUSSION (no camera-direct address). Veera medium close-up, head turned slightly toward Amit at her left, hand sweeping across the panel as she names the story. Scans from Amit to Anjali on 'three views'. Authoritative measured cadence.
```

### 3. Chain clause (r2v-dual mode only)
```
IMAGE REFERENCES (priority: @Image1 dominates identity, @Image2 is setup-only):
@Image1 is the GROUND TRUTH for everything in this scene — use it for camera framing, set composition, lighting, panelist faces, panelist ages, panelist wardrobes, panelist hair, panelist seat positions, and the overall photoreal rendering style. Every panelist visible in this shot must match @Image1 EXACTLY in face, age, hair, and wardrobe.

@Image2 is a SETUP REFERENCE ONLY — use it ONLY to confirm the desk shape, the THE TABLOID backdrop, the studio lighting design, and which panelist sits in which seat position (left-to-right order). DO NOT use @Image2 for faces. DO NOT use @Image2 for the rendering style. DO NOT blend @Image2's facial features into the panelists. DO NOT change anyone's seating position from what is in @Image1. If @Image1 and @Image2 disagree on visual style, ALWAYS follow @Image1.

DO NOT make this scene more cartoon-stylized than @Image1. Keep the photorealistic broadcast look established by @Image1.
```

### 4. AUDIO clause
```
AUDIO: Studio dialogue only. The speaker's voice over clean room tone. NO music, NO sound effects, NO dramatic stings, NO whip-pan whooshes, NO gunshots, NO explosions, NO breaking glass, NO action-movie ambient. Treat this as a calm news-debate set.
```

### 5. SPOKEN LINE clause (verbatim VO line included)
```
SPOKEN LINE — DELIVER VERBATIM. The featured speaker speaks this line in full within the 5-second clip. DO NOT paraphrase. DO NOT trim. DO NOT shorten. DO NOT add words. DO NOT summarize. Lip-sync the speaker's mouth to these exact words at a natural broadcast pace.
Veera Naatchi: "Skyroot just flagged Vikram One off, to Sri Hari Kota. Telangana's Chief Minister attended. Launch window, is June. Three views, one rocket."
```

### 6. Motion + framing tail (added by seedance.py wrapper)
```
Camera motion: locked, no camera movement — held frame. Held frame from start to end. Do not re-light or re-stage. Aspect ratio: 9:16. Broadcast-polished composition.
```
