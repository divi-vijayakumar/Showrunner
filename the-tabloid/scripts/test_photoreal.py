"""One-off test: can BytePlus Seedance i2v accept a Seedream-generated
photoreal portrait? Talks to BytePlus ARK directly. Costs roughly $0.50
(1 Seedream image + 1 Seedance 5s clip).

Run from repo root:
    cd the-tabloid
    backend/.venv/bin/python scripts/test_photoreal.py

Outcomes:
  - Seedream 422 on portrait        → photoreal blocked at the image stage
  - Seedance 422 on i2v             → photoreal blocked at the video stage
  - Both succeed                    → photoreal pipeline is viable
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings


PHOTOREAL_PORTRAIT_PROMPT = """
Cinematic photorealistic portrait of Veera Naatchi, a fictional Indian-English
broadcast journalist in her late 30s — host of THE TABLOID, a satirical news
debate show. SHE IS NOT A REAL PERSON. AI-generated fictional character.

Sharp shoulder-length straight black hair with a confident side part. One
statement gold ear-cuff, otherwise minimal jewelry. Tailored deep-burgundy
structured blazer over a crisp white shell. Bold lip color. Composed,
slightly forward-leaning posture. Direct, intelligent gaze. No smile in
resting frame — strategic, not theatrical.

Modern news-studio set: out-of-focus screens behind her, subtle THE TABLOID
glow on the back wall. Strong key light from camera-left, soft fill, dark
studio background. Medium close-up framing, eye-line locked to camera,
shallow depth of field.

9:16 vertical. High-resolution professional studio photography, sharp focus,
natural skin texture. Photorealistic.
""".strip()


SEEDANCE_I2V_PROMPT = """
The host Veera Naatchi at the curved anchor desk of THE TABLOID. Slight
forward lean, eyes locked to the lens, composed posture. She gestures
decisively toward camera as she names the question viewers are actually
asking. Strategic intensity, no smile.

Camera motion: slow dolly in (push) toward the speaker. Start moving at
clip open, decelerate over the final 0.8 seconds, end on a stable held
frame.

Studio dialogue only. Resolution: 1080p. Aspect ratio: 9:16.
""".strip()


async def gen_seedream_portrait() -> str:
    """Direct ARK Seedream call. Returns image URL on success."""
    cfg = settings()
    payload = {
        "model": cfg.seedream_model,
        "prompt": PHOTOREAL_PORTRAIT_PROMPT,
        "size": "1024x1820",
        "response_format": "url",
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {cfg.byteplus_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{cfg.seedream_base_url.rstrip('/')}/images/generations"
    print(f"  POST {url}")
    print(f"  model={cfg.seedream_model}")

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            print(f"\n  >>> SEEDREAM REJECTED: HTTP {resp.status_code}")
            print(f"  >>> Response: {resp.text[:1500]}")
            sys.exit(2)
        data = resp.json()

    arr = data.get("data") or data.get("images") or []
    img_url = (arr[0].get("url") if arr else None) or data.get("url") or ""
    if not img_url:
        print(f"\n  >>> SEEDREAM no URL in response: {data}")
        sys.exit(3)
    return img_url


async def gen_seedance_clip(image_url: str) -> str:
    """Direct ARK Seedance call. Returns video URL on success."""
    cfg = settings()
    payload = {
        "model": cfg.seedance_model,
        "content": [
            {"type": "text", "text": SEEDANCE_I2V_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "first_frame",
            },
        ],
        "ratio": "9:16",
        "duration": 5,
        "generate_audio": False,
        "watermark": False,
    }
    headers = {
        "Authorization": f"Bearer {cfg.byteplus_api_key}",
        "Content-Type": "application/json",
    }
    base = cfg.byteplus_base_url.rstrip("/")
    create_url = f"{base}/contents/generations/tasks"
    print(f"  POST {create_url}")
    print(f"  model={cfg.seedance_model}")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(create_url, headers=headers, json=payload)
        if resp.status_code >= 400:
            print(f"\n  >>> SEEDANCE CREATE REJECTED: HTTP {resp.status_code}")
            print(f"  >>> Response: {resp.text[:1500]}")
            sys.exit(4)
        data = resp.json()
        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            print(f"\n  >>> SEEDANCE no task id: {data}")
            sys.exit(5)
        print(f"  task created: {task_id}")

        poll_url = f"{base}/contents/generations/tasks/{task_id}"
        deadline = asyncio.get_event_loop().time() + 4 * 60
        delay = 3.0
        while asyncio.get_event_loop().time() < deadline:
            r = await client.get(poll_url, headers=headers)
            if r.status_code >= 400:
                print(f"\n  >>> SEEDANCE POLL REJECTED: HTTP {r.status_code}")
                print(f"  >>> Response: {r.text[:1500]}")
                sys.exit(6)
            d = r.json()
            status = (d.get("status") or "").lower()
            print(f"  poll: status={status}")
            if status in ("succeeded", "completed", "success"):
                content = d.get("content") or {}
                output = d.get("output") or {}
                vid = (
                    (content.get("video_url") if isinstance(content, dict) else None)
                    or (output.get("video_url") if isinstance(output, dict) else None)
                    or d.get("video_url")
                )
                if not vid:
                    print(f"\n  >>> SEEDANCE no video_url in response: {d}")
                    sys.exit(7)
                return vid
            if status in ("failed", "cancelled", "error"):
                err = (d.get("error") or {}).get("message") or "no detail"
                print(f"\n  >>> SEEDANCE TASK {status}: {err}")
                print(f"  >>> Full response: {d}")
                sys.exit(8)
            await asyncio.sleep(delay)
            delay = min(delay + 1.0, 8.0)
        print("\n  >>> SEEDANCE TIMEOUT (4 min)")
        sys.exit(9)


async def main() -> None:
    cfg = settings()
    print("=" * 60)
    print("Photoreal viability test — Seedream → Seedance i2v")
    print("=" * 60)
    print(f"BytePlus base URL: {cfg.byteplus_base_url}")
    print(f"Seedream base URL: {cfg.seedream_base_url}")
    print(f"Seedream model:    {cfg.seedream_model}")
    print(f"Seedance model:    {cfg.seedance_model}")
    print(f"Mock mode:         {cfg.mock}")
    if cfg.mock:
        print("\n>>> TABLOID_MOCK=1 — this test only makes sense in live mode.")
        sys.exit(1)
    if not cfg.byteplus_api_key:
        print("\n>>> BYTEPLUS_API_KEY not set.")
        sys.exit(1)
    print()

    print("Step 1/2: Photoreal portrait via Seedream...")
    portrait_url = await gen_seedream_portrait()
    print(f"  ✓ Portrait URL: {portrait_url}")
    print()

    print("Step 2/2: Seedance i2v with photoreal first_frame...")
    video_url = await gen_seedance_clip(portrait_url)
    print(f"  ✓ Video URL: {video_url}")
    print()

    print("=" * 60)
    print("RESULT: BOTH MODELS ACCEPTED PHOTOREAL CONTENT")
    print("=" * 60)
    print(f"Portrait: {portrait_url}")
    print(f"Video:    {video_url}")
    print()
    print("Photoreal pipeline is viable on direct BytePlus ARK.")


if __name__ == "__main__":
    asyncio.run(main())
