# Setup — keys, services, env

Do these once before you flip off `TABLOID_MOCK`. Takes ~30 minutes end-to-end.

## 1. BytePlus (Seed 2.0 LLM + Seedance 2.0 video + Seed Speech TTS)

All three services live behind one BytePlus console account.

1. Sign up at https://console.byteplus.com and verify the org.
2. **Seed 2.0 (LLM brain)** — enable in the Models/Seed section. The chat-completions endpoint we call is `POST {SEED_LLM_BASE_URL}/chat/completions`. Copy your API key.
3. **Seedance 2.0 (T2V video)** — enable in the Video/Seedance section. Usually the same API key as Seed. Confirm the base URL from the console page — it sometimes differs between regions.
4. **Seed Speech (TTS)** — enable in the Speech section. This often needs a **separate** API key; copy it.
5. While in Seed Speech, note the available voice IDs — the current code maps our persona `{gender}_{warmth}` knobs to a placeholder voice_id. Once you have real voice IDs, update [`backend/video/seed_speech.py`](backend/video/seed_speech.py) → `_voice_id()` with the real mapping.

Paste into `backend/.env`:

```
BYTEPLUS_API_KEY=<seed + seedance key>
BYTEPLUS_BASE_URL=https://api.byteplus.com/seedance/v1
SEED_LLM_BASE_URL=https://api.byteplus.com/seed/v1
SEED_LLM_MODEL=seed-2.0

SEED_SPEECH_API_KEY=<speech key>
SEED_SPEECH_BASE_URL=https://api.byteplus.com/speech/v1
```

## 2. Firebase (Firestore + Storage)

1. https://console.firebase.google.com → **Add project**. Give it a name, disable Google Analytics.
2. **Build → Firestore Database → Create database** → Production mode → pick a region (us-central1 is fine).
3. **Build → Storage → Get Started** → same region. This is where the final .mp4 files live. For the demo you can keep rules open:
   ```
   rules_version = '2';
   service firebase.storage {
     match /b/{bucket}/o {
       match /{allPaths=**} {
         allow read: if true;
         allow write: if false;
       }
     }
   }
   ```
4. **Project settings → Service accounts → Generate new private key** — downloads a JSON. Put it at `backend/firebase-service-account.json` (the path is in `.gitignore`, do not commit it).
5. **Project settings → General → Your apps → Add app → Web** → register, copy the config object.

Paste into `backend/.env`:
```
FIREBASE_PROJECT_ID=<project id>
FIREBASE_STORAGE_BUCKET=<project id>.appspot.com
GOOGLE_APPLICATION_CREDENTIALS=./firebase-service-account.json
```

Paste the Web config into `frontend/.env`:
```
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=<project>.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=<project>
VITE_FIREBASE_STORAGE_BUCKET=<project>.appspot.com
VITE_FIREBASE_APP_ID=...
```

## 3. Redis (for Celery, prod only — mock mode doesn't need it)

Local: `brew install redis && redis-server --daemonize yes`. In prod, any managed Redis is fine; just set `REDIS_URL` in `backend/.env`.

## 4. Flip to production mode

```
# backend/.env
TABLOID_MOCK=0
```

Then restart uvicorn and the Celery worker:
```
cd the-tabloid
redis-server --daemonize yes
backend/.venv/bin/celery -A backend.jobs.pipeline worker --loglevel=info --pool=solo
backend/.venv/bin/uvicorn backend.main:app --port 8000
```

In mock mode (`TABLOID_MOCK=1`) the pipeline runs in-process as a FastAPI background task — no Celery/Redis needed. As soon as you flip to `0`, the API dispatches to Celery so heavy work doesn't block requests.

## 5. Gemini? (not wired yet — needs a decision)

Gemini isn't in the current pipeline. Two reasonable ways to add it, each has tradeoffs:

- **As an LLM alternative** — use Gemini 2.x instead of Seed 2.0 for story selection, debate, script. Simple to swap behind a single `call_llm()` boundary in [`backend/agents/llm.py`](backend/agents/llm.py). Upside: Google's grounding makes story selection more accurate. Downside: two vendor accounts to manage for the hackathon.
- **As a research grounding tool** — keep Seed 2.0 as the voice, but use Gemini (with search grounding enabled) to fetch up-to-date facts + article summaries that the debate agents reference. Upside: best of both worlds — Seed's speed + Gemini's fresh facts. Downside: one more API call per segment (~3–5s latency).

Tell me which one you want and I'll wire it.

## 6. Verify

```
curl http://localhost:8000/health         # {"status":"ok","mock":"off"}
curl -X POST http://localhost:8000/api/generate/ai_updates -H 'Content-Type: application/json' -d '{}'
# watch the segment doc appear in Firestore console → `segments/{id}`
# watch messages stream in under `segments/{id}/messages`
# final mp4 lands in Storage as `segments/{id}.mp4`
```
