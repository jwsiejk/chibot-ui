# WS_PHASE_PLAN

> **Purpose**: Migrate Ask Chip from the hybrid HTTP+WS audio lane to a **WebSocket‑only** design that mirrors Deepgram’s realtime WebSocket patterns (binary audio frames, `{"type":"CloseStream"}` to end a turn, interim/final `Results` with `is_final`, `UtteranceEnd`, and keep‑alive). This document is the **single source of truth** for scope, protocol, phases, acceptance criteria, and runbook notes.

---

## High‑Level Overview

- **One socket per tab**: `/ws/v1/chat` carries **everything** — mic audio (binary), control (JSON), assistant output/events (JSON), and optionally streamed TTS (binary).
- **Delete** HTTP mic routes: `/api/v1/voice/chunk`, `/api/v1/voice/end`. No fallbacks.
- **Keep** existing v1 HTTP routes: `/api/v1/greet`, `/api/v1/chat`, `/api/v1/voice/tts-with-visemes`, `/api/v1/admin/logs` (SSE).
- **Provider semantics**: Follow Deepgram’s WS shapes to the letter: binary audio frames, `{"type":"CloseStream"}`, interim `Results` (is_final=false), final `Results` (is_final=true), optional `UtteranceEnd`, keep‑alive.

Non‑goals:
- No HTTP chunking or end‑of‑turn POSTs.
- No alternate vendor fallbacks.

---

## WebSocket Message Schema (`/ws/v1/chat`)

### Client → Server (Text JSON)

- **Configure** *(optional; mirrors Deepgram listen flags; sent once after connect)*
  ```json
  {
    "type": "Configure",
    "encoding": "opus",
    "sample_rate": 48000,
    "channels": 1,
    "interim_results": true,
    "smart_format": true,
    "punctuate": true,
    "vad_events": true,
    "utterance_end_ms": 1200
  }
  ```

- **Close current user turn**
  ```json
  {"type":"CloseStream"}
  ```

- **Keep alive during long silences** *(every ~4s)*
  ```json
  {"type":"KeepAlive"}
  ```

- **Barge‑in / cancel current assistant speech** *(app‑level convenience)*
  ```json
  {"type":"BargeIn"}
  ```

### Client → Server (Binary)

- **Mic audio slices** — `ArrayBuffer` from `MediaRecorder('audio/webm;codecs=opus')` at ~150–200 ms cadence.
- **First blob must include the WebM/Opus header** (send blobs unmodified).

### Server → Client (Text JSON)

- **Pass‑through interim/final transcripts (Deepgram Results)**
  ```json
  {
    "type": "Results",
    "channel": {
      "alternatives": [
        { "transcript": "example partial...", "confidence": 0.93 }
      ],
      "is_final": false
    }
  }
  ```
  When `is_final: true`, that span is finalized.

- **Utterance boundary (if VAD enabled)**
  ```json
  { "type":"UtteranceEnd" }
  ```

- **Errors**
  ```json
  { "type":"Error", "code":"...", "message":"..." }
  ```

### Server → Client (Binary, optional)

If you choose to stream ElevenLabs TTS back on the same socket:
```
{"type":"TTSStart","turn_id":"..."}
(binary audio chunk)
(binary audio chunk)
{"type":"TTSEnd","turn_id":"..."}
```
Otherwise, keep TTS over `/api/v1/voice/tts-with-visemes` (HTTP) — independent of this plan.

---

## Phase Plan

### Phase 0 — Remove Hybrid & Lock Surfaces
**Tasks**
- Delete `/api/v1/voice/chunk`, `/api/v1/voice/end`, helpers, and tests.
- Remove any feature flag that enables HTTP audio.
- Route‑linter: fail build on any `/api/v1/voice/*` (except TTS) or legacy routes.

**Acceptance**
- Grep shows **no** references to chunk/end routes.
- Linter blocks re‑introduction.

---

### Phase 1 — Protocol Contract (Schema) + Docs
**Tasks**
- Check in this file as `docs/WS_PHASE_PLAN`.
- Add `docs/ws_protocol.md` (or link to this section) with examples.
- FE/BE agree on JSON keys and binary behavior (no first‑blob coalescing).

**Acceptance**
- Contract doc present; unit tests serialize/parse text frames; binary vs text demux validated.

---

### Phase 2 — Backend WS Handler (Multiplexer)
**Tasks**
- In `/ws/v1/chat`:
  - Accept socket; bind session.
  - `async for msg in websocket`: demux **text** (JSON) vs **binary** (audio).
  - Maintain per‑connection ASR state and an `asyncio.Lock` + `asyncio.Event` to gate `open→send`.
  - On first audio: open provider stream (once), set `opened=true`, emit `asr_open` (also to Admin SSE).
  - On each binary: forward to provider.
  - On `CloseStream`: send provider close, linger ~600 ms, wait final (≤ 8 s), emit `UtteranceEnd` if provider sends it.
  - Ping/pong (server) every 25–30 s; drop stale sockets.

**Acceptance**
- Exactly **one** provider open per WS connection.
- No `send_before_open` warnings under load.
- Final emitted within bound after `CloseStream`.

---

### Phase 3 — Provider Glue (Deepgram Semantics)
**Tasks**
- Build provider URL using defaults or `Configure` frame flags (`encoding`, `sample_rate`, `channels`, `interim_results`, `vad_events`, `utterance_end_ms >= 1000`).
- Forward audio frames as binary.
- Pass through `Results` messages; surface `is_final` faithfully.
- Emit `UtteranceEnd` when provider signals end‑of‑speech.

**Acceptance**
- Interims start within ~1 s of speech; final follows `CloseStream`.
- Logs show the expected sequence: `provider_open → asr_open → (Results...) → final → UtteranceEnd`.

---

### Phase 4 — Frontend Mic Sender (WS‑only)
**Tasks**
- Replace POST loop with WS sender:
  - `const ws = new WebSocket(wssUrl);` — reuse existing socket.
  - `MediaRecorder(...).start(150 or 200)`; `ondataavailable` → `ws.send(await blob.arrayBuffer())`.
  - Send `{"type":"KeepAlive"}` every ~4 s if not speaking.
  - Send `{"type":"CloseStream"}` to end user turn.
  - Backpressure: if `ws.bufferedAmount` > threshold, `rec.pause()`; resume when drained.
  - Barge‑in: send `{"type":"BargeIn"}`; stop/pause TTS immediately.

- UI state:
  - “Listening” → display interims from `Results (is_final:false)`.
  - “Thinking” → after `UtteranceEnd` or `CloseStream`.
  - “Responding” → when TTS starts.

**Acceptance**
- First blob (header) goes out unmodified.
- Interims render while speaking; barge‑in cuts TTS ≤ 250 ms.

---

### Phase 5 — Persona, Memory, TTS (No Regression)
**Tasks**
- Keep ElevenLabs TTS + visemes.
- Maintain persona (Nebraska 12–15%) and teacher‑move policy.
- Summarize context every few turns; maintain a sliding window.

**Acceptance**
- Persona tone stable; long sessions (≥ 30 min) maintain coherence within token budgets.

---

### Phase 6 — Diagnostics & Admin
**Tasks**
- Diagnostics watches Admin SSE (or chat WS) for `asr_partial`, `asr_final`, `asr_error`, tied to the current `session_id`.
- “Record 5 s” prompt workflow (visual confirmation) — then report partials/finals.

**Acceptance**
- “Pipe alive” → green on `asr_open` + ≥1 audio frame.
- Partial/final counters increment correctly.

---

### Phase 7 — Reliability, Security, Cost
**Reliability**
- Inbound queue cap ≈ 1–2 s of audio; drop oldest on overflow.
- Graceful shutdown: drain & close sockets on deploy.
- Reconnect policy: UI snaps to **Ready** on disconnect.

**Security**
- Cookie session at WS handshake; **Origin** check.
- No CSRF for WS frames (documented).
- Redact PII in logs; rate‑limit tokens/seconds.

**Cost**
- Track per‑session ASR minutes, TTS minutes, LLM tokens; enforce caps with friendly UI.

**Acceptance**
- 2‑hour soak without leaks or stalls.
- Idle tabs with keep‑alive remain stable (or reconnect cleanly).

---

### Phase 8 — Performance Targets & Tuning
- **Interim time‑to‑first**: ≤ 0.8–1.2 s after speech start.
- **Barge‑in cut**: ≤ 150–250 ms.
- **Final after close**: ≤ 8 s bound.
- **Tunables**: `utterance_end_ms` 1200–1500; linger 600 ms; timeslice 150–200 ms; WS ping 25–30 s; KeepAlive ~4 s.

**Acceptance**
- Measured on wired/wifi; doc numbers checked into a perf readme.

---

### Phase 9 — Deploy & Runbook
**Render**
- **Web Service** (ASGI / Gunicorn+Uvicorn), bind `$PORT`, WS enabled.
- Start with `WEB_CONCURRENCY=1`; scale after stability.
- Health: `/api/v1/health` returns `{ok:true}`.

**Runbook**
- “No interims” → check first blob header, provider URL flags.
- “Idle close” → ensure KeepAlive & ping/pong.
- “send_before_open” → verify lock/event in WS handler.
- “Final timeout” → tune `utterance_end_ms`, linger and final wait; check network.

**Acceptance**
- Blue/green deploy, rollback tested.

---

## Server WS Handler — Copy/Paste Skeleton (Python, ASGI‑style)

> This is framework‑agnostic pseudo‑code close to FastAPI/Starlette patterns. Replace `ProviderClient` with your Deepgram bridge.

```python
# app/ws_chat.py
import asyncio, json, logging
from typing import Optional

log = logging.getLogger(__name__)

class ProviderClient:
    def __init__(self, cfg): self.cfg = cfg
    async def open(self): ...
    async def send_chunk(self, data: bytes): ...
    async def close_stream(self): ...              # send {"type":"CloseStream"}
    async def aclose(self): ...                    # close provider socket
    def on_results(self, cb): ...                  # cb(dict)
    def on_utterance_end(self, cb): ...
    def on_error(self, cb): ...

class WsSession:
    def __init__(self):
        self.provider: Optional[ProviderClient] = None
        self.opening = asyncio.Event()
        self.opened = False
        self.open_lock = asyncio.Lock()
        self.cfg = {
            "encoding":"opus","sample_rate":48000,"channels":1,
            "interim_results":True,"smart_format":True,"punctuate":True,
            "vad_events":True,"utterance_end_ms":1200
        }

    async def ensure_open(self, send_to_client):
        if self.opened: return
        async with self.open_lock:
            if self.opened: return
            self.opening.clear()
            self.provider = ProviderClient(cfg=self.cfg)
            # Wire provider callbacks → client WS
            self.provider.on_results(lambda j: asyncio.create_task(send_to_client(j)))
            self.provider.on_utterance_end(lambda: asyncio.create_task(send_to_client({"type":"UtteranceEnd"})))
            self.provider.on_error(lambda e: asyncio.create_task(send_to_client({"type":"Error","message":str(e)})))
            await self.provider.open()
            self.opened = True
            self.opening.set()
            await send_to_client({"type":"asr_open"})  # optional app event

    async def close_turn(self):
        if self.provider:
            await self.provider.close_stream()

    async def aclose(self):
        if self.provider:
            try: await self.provider.aclose()
            except: pass
        self.opened = False

# ASGI/Starlette style
async def ws_chat_endpoint(websocket):
    await websocket.accept()
    sess = WsSession()
    keepalive_task = None

    async def send_json(obj: dict):
        await websocket.send_text(json.dumps(obj))

    try:
        # WS ping/pong managed by server; add periodic keepalive if desired on server side
        while True:
            msg = await websocket.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                data = msg["bytes"]
                if not sess.opened:
                    # open lazily; gate until open completes
                    await sess.ensure_open(send_json)
                await sess.provider.send_chunk(data)
                continue

            if "text" in msg and msg["text"] is not None:
                try:
                    j = json.loads(msg["text"])
                except Exception:
                    continue

                t = j.get("type")
                if t == "Configure":
                    # merge known keys only
                    for k in ("encoding","sample_rate","channels","interim_results","smart_format","punctuate","vad_events","utterance_end_ms"):
                        if k in j: sess.cfg[k] = j[k]
                elif t == "CloseStream":
                    await sess.close_turn()
                elif t == "KeepAlive":
                    # No‑op on server; provider keepalive handled internally if needed
                    pass
                elif t == "BargeIn":
                    # App‑level: stop TTS immediately (not shown) + end current turn
                    await sess.close_turn()
                else:
                    # ignore unknown types
                    pass

    except Exception as e:
        log.warning("WS error: %s", e)
    finally:
        await sess.aclose()
        try: await websocket.close()
        except: pass
```

---

## Frontend Mic Sender — Copy/Paste Skeleton (Vanilla JS)

```html
<script>
(function(){
  const state = { ws:null, rec:null, keepaliveId:null };
  const url = (location.protocol==="https:"?"wss://":"ws://") + location.host + "/ws/v1/chat";

  function connect(){
    if(state.ws && (state.ws.readyState===WebSocket.OPEN || state.ws.readyState===WebSocket.CONNECTING)) return state.ws;
    const ws = new WebSocket(url);
    state.ws = ws;

    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      // Optional Configure, mirrors provider flags
      ws.send(JSON.stringify({
        type:"Configure",
        encoding:"opus", sample_rate:48000, channels:1,
        interim_results:true, smart_format:true, punctuate:true,
        vad_events:true, utterance_end_ms:1200
      }));
      // Client keepalive
      clearInterval(state.keepaliveId);
      state.keepaliveId = setInterval(()=>{
        try{ ws.send(JSON.stringify({type:"KeepAlive"})); }catch(_){}
      }, 4000);
    };

    ws.onmessage = (ev)=>{
      if(typeof ev.data === "string"){
        try{
          const j = JSON.parse(ev.data);
          if(j.type==="Results"){
            const alt = j.channel && j.channel.alternatives && j.channel.alternatives[0];
            const text = alt && alt.transcript || "";
            const isFinal = !!(j.channel && j.channel.is_final);
            // TODO: render interim/final
          }else if(j.type==="UtteranceEnd"){
            // TODO: switch UI from Listening → Thinking
          }else if(j.type==="Error"){
            console.warn("ASR error", j);
          }
        }catch(_){}
      }else{
        // binary down (e.g., TTS chunk) — optional
      }
    };

    ws.onclose = () => {
      clearInterval(state.keepaliveId);
      state.keepaliveId = null;
      // TODO: set UI → Ready
    };

    return ws;
  }

  async function startMic(){
    const ws = connect();
    if(ws.readyState!==WebSocket.OPEN){
      await new Promise(res => ws.addEventListener("open", res, {once:true}));
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio:{ echoCancellation:true, noiseSuppression:true, channelCount:1, sampleRate:48000 }
    });
    const rec = new MediaRecorder(stream, { mimeType:"audio/webm;codecs=opus", audioBitsPerSecond:128000 });

    rec.ondataavailable = async (ev)=>{
      if(ev.data && ev.data.size>0){
        const buf = await ev.data.arrayBuffer();
        // Backpressure
        if(ws.bufferedAmount > 256*1024){ // 256 KiB
          try{ rec.pause(); }catch(_){}
          const waitDrain = () => new Promise(r=> setTimeout(r, 50));
          while(ws.bufferedAmount > 64*1024) await waitDrain();
          try{ rec.resume(); }catch(_){}
        }
        ws.send(buf);
      }
    };
    rec.start(200); // 150–200ms cadence
    state.rec = rec;
    state.stream = stream;
  }

  function stopMicAndCloseTurn(){
    try{ state.rec && state.rec.stop(); }catch(_){}
    try{ state.stream && state.stream.getTracks().forEach(t=>t.stop()); }catch(_){}
    state.rec = null; state.stream = null;
    try{ state.ws && state.ws.send(JSON.stringify({type:"CloseStream"})); }catch(_){}
  }

  function bargeIn(){
    // Stop any local TTS playback (not shown), then notify server
    try{ state.ws && state.ws.send(JSON.stringify({type:"BargeIn"})); }catch(_){}
  }

  // Expose minimal API
  window.WS_ONLY_AUDIO = { connect, startMic, stopMicAndCloseTurn, bargeIn };
})();
</script>
```

---

## Acceptance Checklist (Roll‑Up)

- **Routes**: No `/api/v1/voice/chunk|end` anywhere. Linter blocks them.
- **WS handler**: One provider `open` per WS; no “send_before_open”; final within bound after `CloseStream`; ping/pong + keepalive active.
- **Frontend**: First blob intact; interims render live; barge‑in cuts TTS ≤ 250 ms; backpressure handled.
- **Admin/Diag**: Partial/final events visible via Admin SSE; diagnostics shows “pipe alive” and counts.
- **Perf**: Interim ≤ 1.2 s; final after close ≤ 8 s; long sessions stable; idle handling proven.
- **Security/Cost**: Origin check; cookie auth; redaction; limits for ASR/TTS/LLM minutes/tokens.

---

## Notes on Deepgram Conformance

- **Turn close**: `{"type":"CloseStream"}` → provider flush & final.  
- **Binary audio**: first blob contains header; small slices ~150–200 ms.  
- **Results**: pass through `Results` (with `channel.alternatives[].transcript`, `is_final`).  
- **VAD**: `vad_events=true`, `utterance_end_ms >= 1000` to get `UtteranceEnd`.  
- **Keepalive**: client text keep‑alive every few seconds; server ping/pong ~25–30 s.

This plan aligns message shapes and lifecycle with Deepgram’s realtime WS examples to avoid custom semantics.

---

## Render Runbook (Quick)

- **Process**: Gunicorn/Uvicorn (ASGI), bind `$PORT`, WS enabled.
- **Scale**: Start with `WEB_CONCURRENCY=1`; increase after stability.
- **Health**: `/api/v1/health` returns `{ok:true}`; graceful shutdown drains sockets.
- **Env**: No changes — keep `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, DB vars, etc.

---

*End of WS_PHASE_PLAN.*
