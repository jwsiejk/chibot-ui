# OPERATING_INSTRUCTIONS (WS‑Only Edition)

**Status:** Production | **Transport:** WebSocket‑only (no HTTP mic ingest) | **API surface:** v1‑only

This document explains how to run, monitor, and troubleshoot Ask Chip after the migration to a **WebSocket‑only** audio lane that mirrors Deepgram’s realtime WS patterns. It replaces any prior instructions that referenced HTTP `/api/v1/voice/chunk` or `/api/v1/voice/end`.

---

## 1) Scope & Audience
- **Operators:** start/stop the service, deploy on Render, check health, inspect logs.
- **Admins:** use the in‑app Admin Console, review live logs (SSE), toggle runtime settings.
- **Developers:** understand message flow, where to modify WS handler & mic sender, and how to run diagnostics.

---

## 2) System Overview (Post‑Migration)
- **One socket per tab:** `wss://<host>/ws/v1/chat` carries:
  - **Binary** mic audio frames (WebM/Opus)
  - **Text JSON** control (e.g., `CloseStream`, `KeepAlive`, `BargeIn`, optional `Configure`)
  - **Assistant events** (e.g., interim/final transcripts, `UtteranceEnd`, errors)
  - *(Optional)* streamed TTS audio (binary) if enabled
- **HTTP (unchanged):**
  - `POST /api/v1/greet`
  - `POST /api/v1/chat`
  - `POST /api/v1/voice/tts-with-visemes` (if TTS stays over HTTP)
  - `GET  /api/v1/admin/logs` (Server‑Sent Events; live admin log stream)
  - `GET  /api/v1/health` (basic readiness probe)
- **Removed (no fallbacks):**
  - `POST /api/v1/voice/chunk` ❌
  - `POST /api/v1/voice/end` ❌

---

## 3) Message Schema (Summary)
**Client → Server (Text JSON)**
- `{"type":"Configure","encoding":"opus","sample_rate":48000,"channels":1,"interim_results":true,"smart_format":true,"punctuate":true,"vad_events":true,"utterance_end_ms":1200}` *(optional, once)*
- `{"type":"CloseStream"}` — end the user turn (server forwards provider close)
- `{"type":"KeepAlive"}` — send every ~4s during long silences
- `{"type":"BargeIn"}` — app‑level: stop TTS, close current turn, prep next

**Client → Server (Binary)**
- **Mic audio slices** produced by `MediaRecorder('audio/webm;codecs=opus')` at 150–200 ms cadence. **First blob must include the container header** (send blobs unmodified).

**Server → Client (Text JSON)**
- `{"type":"Results","channel":{"alternatives":[{"transcript":"...","confidence":0.93}],"is_final":false}}` *(pass‑through interim)*
- `{"type":"Results", ... "is_final":true}` *(final for the span)*
- `{"type":"UtteranceEnd"}` *(when VAD is enabled and the gap crosses `utterance_end_ms`)*
- `{"type":"Error","code":"...","message":"..."}`

**Server → Client (Binary, optional)**
- TTS streaming over WS (if enabled): `TTSStart` / binary chunks / `TTSEnd`.

> Full protocol details live in **docs/WS_PHASE_PLAN**.

---

## 4) Quick Start (Local)
**Prereqs**
- Python 3.10+ (or container runtime if using Docker)
- Environment variables set (see §6)
- Ports open locally

**Run (ASGI via Uvicorn/Gunicorn)**
```bash
# dev
uvicorn app.asgi_gateway:asgi --host 0.0.0.0 --port 8000 --reload

# prod‑like
gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:8000 app.asgi_gateway:asgi
```

**Open the app**
- `http://localhost:8000` (main UI)
- Admin Console (in‑app): Admin menu → Diagnostics / Logs

---

## 5) Render Deployment
**Service type:** Web Service (Docker *or* Native Build)  
**Command:** (example)
```bash
gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:$PORT app.asgi_gateway:asgi
```
**Notes**
- Start with `WEB_CONCURRENCY=1`. Increase after stability testing.
- WebSockets are supported on Render Web Services by default.
- Health check: `GET /api/v1/health` returns `{"ok":true}`.
- If running a headless agent alongside the web process, ensure the web process still binds to `$PORT` and stays healthy.

---

## 6) Environment Variables (minimum)
- `OPENAI_API_KEY` — LLM
- `ELEVENLABS_API_KEY` — TTS
- `DATABASE_URL` — Neon (Postgres)
- `SECRET_KEY` — Flask session secret
- `FEATURE_*` — Any runtime feature toggles you use (persona %, suggestions, etc.)
- *(Optional, provider flags)* If you externalize ASR flags, keep them server‑side defaults; `Configure` can override per connection.

> Keep all route/feature names aligned with the “v1‑only” policy — **no legacy routes**.

---

## 7) How a Call Works (Runtime State)
1. **Connect** `/ws/v1/chat` (cookie auth; origin checked).  
2. **(Optional) Configure** — client sends `Configure` once; server dials provider WS flags.  
3. **Listening** — client streams binary mic slices every 150–200 ms.  
4. **Interims** — server relays interim `Results` to the client; UI shows live transcription.  
5. **CloseStream** — client ends the user turn; server sends provider close, lingers ~600 ms, waits for **final** (≤ 8 s).  
6. **Thinking → Responding** — LLM + TTS; visemes sync the avatar.  
7. **Barge‑in** — user starts talking; client sends `BargeIn` and pauses/stops TTS immediately; next turn begins.

---

## 8) Admin & Diagnostics
- **Admin Log (SSE)**: `GET /api/v1/admin/logs` — live JSON events, e.g., `asr_open`, `Results (is_final)`, `UtteranceEnd`, `asr_error`, `tts_start/tts_end`.
- **Diagnostics flow** (WS‑only):
  - “Press Continue to record 5 s” → “Recording…” → “Audio captured (sending)…”.  
  - Rows: **pipe alive**, **partials_seen**, **final_seen**, **admin_sse_ok**, **tts_cancel_ok**.
- **What green looks like**: one `provider_open`, one `asr_open`, several `Results (is_final:false)`, one final, maybe `UtteranceEnd`.

---

## 9) Operational Runbook
**A) No interims (silence or gibberish)**
- Ensure **first blob** is unmodified (header present).  
- Verify provider URL flags (encoding=opus, sample_rate=48000, channels=1, `interim_results=true`).  
- Check network egress to provider.

**B) “send_before_open” in logs**
- Verify WS handler gates `open→send` with a lock and sets an “opened” event before allowing chunks.

**C) Final timeout after `CloseStream`**
- Set `utterance_end_ms` ~1200–1500; linger ~600 ms; final wait ≤ 8 s.  
- Confirm the client actually sent `CloseStream` (see Admin SSE).

**D) Idle socket closes unexpectedly**
- Client should send `{"type":"KeepAlive"}` every ~4 s.  
- Server ping/pong every ~25–30 s; drop stale connections.

**E) Backpressure / audio backlog**
- If `ws.bufferedAmount` grows > ~256 KiB on the client, pause recorder; resume when drained.  
- Cap server inbound queue to ~1–2 s of audio; drop oldest if needed (with a metric).

**F) Mobile/Background tabs**
- Safari iOS: require a user gesture before audio playback.  
- Background tabs throttle timers; show a “Paused; tab inactive” banner and resume on focus.

---

## 10) Security & Compliance
- Cookie‑based auth; **Origin** check at WS handshake.
- No CSRF on WS frames; keep CSRF for HTTP POST routes.
- PII redaction in logs; do not log transcripts verbatim in error paths.
- Rate limiting: per‑session LLM tokens, ASR minutes, TTS minutes; clear UI errors when caps are exceeded.

---

## 11) Performance Targets (initial)
- **Interim time‑to‑first:** ≤ 0.8–1.2 s after speech start.
- **Barge‑in cut time:** ≤ 150–250 ms to stop TTS.
- **Final after `CloseStream`:** ≤ 8 s hard cap.

Tune:
- `utterance_end_ms` 1200–1500 (quick responsiveness vs premature finals)
- MediaRecorder slice 150–200 ms
- Provider linger ~600 ms

---

## 12) Acceptance Checklist (WS‑Only)
- ❑ No `/api/v1/voice/chunk|end` routes (linter enforced)  
- ❑ Exactly one provider open per WS; **no** “send_before_open”  
- ❑ Interims appear during speech; final within bound after `CloseStream`  
- ❑ Barge‑in cancels TTS immediately; next turn accepts audio  
- ❑ Admin SSE shows expected sequence; Diagnostics all green  
- ❑ Security (origin check, redaction) & cost caps in place  
- ❑ Soak test (≥ 2 h) stable; idle keep‑alive proven

---

## 13) File Map (where code lives)
- **WS handler:** `app/.../ws_chat.py` (or your actual WS endpoint module)  
- **Provider bridge:** `app/services/streaming_asr/...` (Deepgram WS client)  
- **Mic sender:** `static/js/...` (client WS module; MediaRecorder → WS)  
- **Admin SSE:** `app/api_v1/admin.py` (logs), `static/js/admin_console.js` (viewer)  
- **Docs:** `docs/WS_PHASE_PLAN` (protocol + phases), **this file**

---

## 14) Decommissioned Endpoints
- `POST /api/v1/voice/chunk` — **removed**  
- `POST /api/v1/voice/end` — **removed**

If any code or tests reference these, treat as a **defect**.

---

*Last updated:* WS‑Only Edition, v1
