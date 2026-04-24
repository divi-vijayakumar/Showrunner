# The Tabloid

AI news debate channel. Pick one of 6 channels, watch 4 AI personas (anchor / provocateur / analyst / humanist) debate today's top story, then see the debate rendered as a short cinematic video.

PWA (installable) · FastAPI + Celery backend · React + Vite + Tailwind frontend · BytePlus Seed 2.0 + Seedance 2.0 + Seed Speech · Firebase Firestore + Storage.

Built for the Beta University Seed Agents Challenge (Silicon Valley finale, May 2 2026).

## Layout

```
the-tabloid/
├── backend/      FastAPI + Celery pipeline
└── frontend/     Vite + React PWA
```

## Quick start (mock mode — no external APIs)

Runs the whole pipeline locally with stubbed LLM / video / TTS / Firestore. Good for UI dev and demo rehearsal.

```bash
# 1. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
echo "TABLOID_MOCK=1" >> .env

# Start redis locally (brew install redis) then:
redis-server --daemonize yes

# Terminal A — Celery worker
celery -A backend.jobs.pipeline worker --loglevel=info

# Terminal B — API
uvicorn backend.main:app --reload --port 8000
```

```bash
# 2. Frontend
cd frontend
npm install
cp .env.example .env
# (leave VITE_FIREBASE_* blank — frontend will poll the backend)
npm run dev
```

Open http://localhost:5173. Without Firestore configured the frontend polls the backend every second for debate updates and segment status — fine for dev.

## Full pipeline (live APIs)

1. Fill in `backend/.env`:
   - `BYTEPLUS_API_KEY` — for Seed 2.0 LLM and Seedance 2.0 video
   - `SEED_SPEECH_API_KEY` + endpoint — for TTS
   - `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, and a service-account JSON at the path in `GOOGLE_APPLICATION_CREDENTIALS`
2. Set `TABLOID_MOCK=0`.
3. Fill in `frontend/.env` with the Firebase Web config (from Firebase console → Project settings → Web app). With it populated, the frontend subscribes directly to Firestore and streams messages in real time.

## Dependencies on the host

- Python 3.11+, Redis, ffmpeg, cairo (for cairosvg) — on Debian/Ubuntu:
  ```
  sudo apt install redis-server ffmpeg libcairo2 libpango-1.0-0
  ```
- Node 20+

## What happens when a user picks a channel

1. `POST /api/generate/{channel}` creates a Firestore `segment` doc and enqueues a Celery task.
2. Task fetches RSS headlines, asks Seed 2.0 which story is most debatable.
3. Runs an 8-turn debate across the 4 personas — streams each line to Firestore as it's produced, so the frontend sees it live.
4. Asks Seed 2.0 to turn the transcript into a 6-scene broadcast script.
5. Generates each scene via Seedance 2.0 (sequentially — 2 rps QPS limit), VO via Seed Speech.
6. Renders infographic overlays (SVG → PNG), stitches everything with ffmpeg, uploads to Firebase Storage.
7. Marks the segment `ready` with the video URL. Frontend auto-switches to the player.

## Notes for hackathon demo

- Trigger the pipeline ~5 minutes before presenting. By stage time the debate is done and the video is landing. You narrate the already-produced transcript, then hit play.
- Pre-generate 2 segments per channel as backup; the video sits in Firebase Storage and loads instantly if anything goes sideways live.
- Seedance at 1080p takes ~60–90s per clip × 6 clips + VO + stitch. Budget 10–12 minutes per full segment.

## Icon placeholders

`frontend/public/icons/{icon-192,icon-512}.png` are generated placeholders (purple radial with a "T"). Swap in real brand icons before shipping to the store — the PWA manifest already points at these paths.
