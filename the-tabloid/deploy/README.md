# Deploy — DigitalOcean droplet (backend) + Netlify (frontend)

Two layers, two hosts. Backend on a $6 droplet, frontend on Netlify free tier.
Plan for ~45 min total the first time.

## Prerequisites
- Droplet you can SSH into. Ubuntu 22.04 LTS or newer assumed.
- A domain (or sub-domain) you can point at the droplet. e.g. `api.tabloid.yourdomain.com`.
- All the dev keys you've been using locally — same ones go on the droplet.

---

## 1. Backend on the droplet

### 1.1 — System deps (one-shot)
```bash
ssh root@<droplet-ip>

apt update && apt -y upgrade
apt -y install python3 python3-venv python3-pip git ffmpeg libcairo2 \
                nginx certbot python3-certbot-nginx ufw

# Create a service user so we don't run as root.
useradd -r -m -d /home/tabloid -s /bin/bash tabloid
mkdir -p /opt/tabloid && chown tabloid:tabloid /opt/tabloid
```

### 1.2 — Pull the repo + venv
```bash
sudo -iu tabloid
cd /opt/tabloid
git clone https://github.com/<you>/SWSCD.git .   # or use ssh
cd /opt/tabloid/the-tabloid

python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt
exit  # back to root
```

### 1.3 — Secrets
```bash
sudo -iu tabloid
cd /opt/tabloid/the-tabloid
cp backend/.env.example backend/.env
nano backend/.env
```

Fill in what you've been using locally:
```
BYTEPLUS_API_KEY=...
SEED_SPEECH_API_KEY=...
OPENROUTER_API_KEY=...
ELEVENLABS_API_KEY=...
GOOGLE_AISTUDIO_API_KEY=...
FIREBASE_PROJECT_ID=theaitabloid
FIREBASE_STORAGE_BUCKET=
GOOGLE_APPLICATION_CREDENTIALS=/opt/tabloid/the-tabloid/backend/firebase-service-account.json
TABLOID_DATA_DIR=/var/tabloid/data
PUBLIC_BASE_URL=https://api.tabloid.yourdomain.com
TABLOID_MOCK=0
LLM_PROVIDER=openrouter
TTS_PROVIDER=elevenlabs    # or google if you've enabled GCP Tier 1
VIDEO_PROVIDER=byteplus
IMAGE_PROVIDER=byteplus
```

Headless droplets can't run `gcloud auth application-default login`, so use a
**service account JSON** instead:

1. Firebase console → Project settings → Service accounts → Generate new private key
2. `scp <downloaded>.json root@<droplet>:/opt/tabloid/the-tabloid/backend/firebase-service-account.json`
3. `chown tabloid:tabloid backend/firebase-service-account.json && chmod 600 backend/firebase-service-account.json`

### 1.4 — Persistent data dir + smoke test
```bash
sudo mkdir -p /var/tabloid/data/audio /var/tabloid/data/videos
sudo chown -R tabloid:tabloid /var/tabloid

# Migrate any local episodes if you scp'd them over.

# Sanity-check uvicorn boots and Firestore auth works:
sudo -iu tabloid
cd /opt/tabloid/the-tabloid
backend/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
# In another shell: curl -s http://127.0.0.1:8000/health  → {"status":"ok","mock":"off"}
# Ctrl-C when satisfied.
exit
```

### 1.5 — systemd service
```bash
cp /opt/tabloid/the-tabloid/deploy/tabloid.service /etc/systemd/system/tabloid.service
systemctl daemon-reload
systemctl enable --now tabloid
systemctl status tabloid          # should be active (running)
journalctl -u tabloid -f          # tail the logs
```

### 1.6 — nginx + SSL
```bash
# Replace api.tabloid.example in the file with your real subdomain BEFORE copy.
cp /opt/tabloid/the-tabloid/deploy/tabloid.nginx /etc/nginx/sites-available/tabloid
sed -i 's/api.tabloid.example/api.tabloid.yourdomain.com/g' /etc/nginx/sites-available/tabloid
ln -s /etc/nginx/sites-available/tabloid /etc/nginx/sites-enabled/tabloid
nginx -t && systemctl reload nginx

# Point your domain's A record at <droplet-ip> (DO DNS or wherever your DNS is).
# Wait ~1 min for DNS to propagate, then:

certbot --nginx -d api.tabloid.yourdomain.com   # answer email + agree
# certbot rewrites the nginx config with HTTPS automatically.
```

### 1.7 — Firewall
```bash
ufw allow ssh
ufw allow 'Nginx Full'
ufw --force enable
```

Backend live at `https://api.tabloid.yourdomain.com/health`.

---

## 2. Frontend on Netlify

### 2.1 — Connect repo
1. https://app.netlify.com → **Add new site → Import an existing project**
2. Pick GitHub → authorize → select **SWSCD**
3. Netlify auto-detects `the-tabloid/frontend/netlify.toml` (already committed) and pre-fills the build settings:
   - Base directory: `the-tabloid/frontend`
   - Build command: `npm ci && npm run build`
   - Publish directory: `dist`

### 2.2 — Env vars
**Site settings → Environment variables → Add a single variable** (one at a time):
```
VITE_API_BASE                  = https://api.tabloid.yourdomain.com
VITE_FIREBASE_API_KEY          = <from your local frontend/.env>
VITE_FIREBASE_AUTH_DOMAIN      = theaitabloid.firebaseapp.com
VITE_FIREBASE_PROJECT_ID       = theaitabloid
VITE_FIREBASE_STORAGE_BUCKET   = theaitabloid.firebasestorage.app
VITE_FIREBASE_APP_ID           = <from your local frontend/.env>
```
Trigger a redeploy from the **Deploys** tab so the env vars take effect.

### 2.3 — Test
Once the build's green, hit `https://<your-site>.netlify.app/`:
- Channel select → Sample → pick India Politics → confirm panel → Start
- Debate streams live (Firestore subscription works through the public Netlify domain)
- Sample renders + audio plays from `https://api.tabloid.yourdomain.com/api/videos/<seg>.mp4`

---

## 3. After-the-deploy hygiene

### Lock down CORS
The backend currently allows `allow_origins=["*"]`. Once the Netlify URL is stable, tighten it. In `backend/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://<your-site>.netlify.app"],
    ...
)
```
Then `git push` → SSH in → `git pull && systemctl restart tabloid`.

### Rotate keys
The dev keys touched local logs, .env files, and your terminal history. Generate fresh production keys for BytePlus / OpenRouter / ElevenLabs and swap them on the droplet only.

### Auto-deploy (optional)
Netlify auto-builds on every push to `main` already. For the backend, the simplest "deploy on push" is a tiny GitHub Action that ssh's in and runs `git pull && systemctl restart tabloid`. Skip until you start iterating on prod.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `502 Bad Gateway` from Netlify console hitting backend | nginx proxy timeout or uvicorn crashed. `journalctl -u tabloid -n 200`. |
| Pipeline `failed` mid-run with `httpx.ReadTimeout` | nginx defaults to 60s; we set `proxy_read_timeout 1800s`. Verify `nginx -T \| grep proxy_read_timeout`. |
| Firestore writes 503 / "Reauthentication" | service-account JSON missing or wrong path. `ls -l /opt/tabloid/the-tabloid/backend/firebase-service-account.json` should be owned by `tabloid`, mode 600. |
| `mock:on` in `/health` despite TABLOID_MOCK=0 | systemd cached an old env. `systemctl daemon-reload && systemctl restart tabloid`. |
| Disk filling up | `du -sh /var/tabloid/data/*` — old episodes pile up; delete oldest or move to S3-compatible storage. |
