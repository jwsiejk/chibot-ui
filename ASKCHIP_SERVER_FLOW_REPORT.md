# AskChip Server Conversational Flow — Current vs Target Design

Goal: Document the current server-side behavior for greet + full-duplex conversation, and define the target design that matches the client-side spec (PHASE.Greet, PHASE.ConversationReady, etc.). Section 2 describes the current implementation with code references; Section 3 defines the target design and maps it to concrete code locations. Section 4 highlights gaps/mismatches; Section 5 outlines an implementation plan.

## 2. Current Behavior — Server-Side, Code-Backed

### 2.1 WebSocket Protocol & Entry Points
- WebSocket entry point: `ChatV2Adapter.__call__` negotiates the subprotocol/token, accepts the connection, then enters the receive loop that dispatches text vs binary frames (`_handle_text` / `_handle_binary`).【F:app/ws/adapter.py†L2465-L2856】
- JSON frame routing:
  - Allowed control types include `input.start`, `start_listening`, `stop_listening`, `client.idle`, `client.log`, `chat.user`, etc., with validation via `_handle_text` before emitting bus events or errors.【F:app/ws/adapter.py†L3230-L3345】
  - `asr.open` requests are ignored until greet completes; otherwise they schedule ASR open or respond with dedup/queued errors depending on state.【F:app/ws/adapter.py†L3346-L3380】
- Binary/audio frames are processed by `_handle_binary`, which enforces size limits, checks whether capture/ASR are armed, and drops/errs when ASR is not ready or accepting audio.【F:app/ws/adapter.py†L3905-L4052】
- Outbound TTS events from the engine land back in the adapter via bus subscriptions; `_handle_tts_start`/`_handle_tts_end` convert them to WebSocket frames such as `tts.start`, audio chunks, `tts.end`, and greet markers.【F:app/ws/adapter.py†L1220-L1333】【F:app/ws/adapter.py†L1341-L1513】

### 2.2 Greet Pipeline (Server-Side)
- Greet trigger: `Engine.start_greet` is called after WS acceptance (`_on_open_and_greet` in the adapter) and asynchronously calls `_maybe_emit_greeting` plus suggestions.【F:app/voice_v2/engine.py†L267-L272】
- Greet generation: `_maybe_emit_greeting` loads persona policy, optionally calls the LLM for greet text (session_open.greet rule), and falls back to persona copy. It publishes policy/LLM/NLG telemetry and emits a `chat.message` assistant frame with `meta.reason="greet"`.【F:app/voice_v2/engine.py†L946-L1078】
- TTS for greet: the greet chat message is consumed by the TTS/streaming path; when the adapter receives `tts.start` with greet metadata, it emits `greet.start` (with `meta.is_greet`) and records `greet_utt_id`.【F:app/ws/adapter.py†L1297-L1324】
- Greet completion: on `tts.end`, the adapter marks `greet_completed=True`, logs a `greet.completed` session step, and emits `greet.complete` with `meta.is_greet`. It also triggers ASR readiness and invokes `enable_full_duplex` on the engine once greet ends.【F:app/ws/adapter.py†L1415-L1497】

### 2.3 ASR/LLM/TTS Turn Pipeline (Conversation)
- ASR ingest: Binary PCM frames reach `_handle_binary`, which checks `client_capture_armed`, `asr_ready`, `session.asr_state == "open"`, and `ctx.accepting_audio` before passing data onward (publication to ASR engine occurs via downstream helpers not shown here). Audio is dropped/errored if ASR is not ready or accepting audio.【F:app/ws/adapter.py†L3953-L4052】
- ASR activation: `_ensure_asr_ready` is gated on greet completion and TTS inactivity; it schedules `_schedule_asr_open` and sends an `asr.ready` bundle only after greet finishes.【F:app/ws/adapter.py†L1220-L1267】
- Text/LLM path: User text (either `chat.user` frames or ASR finals routed elsewhere) is published as `EVT_CHAT_USER` for the engine/LLM stack. Policy/LLM/NLG stages follow the same pipeline used for greet, resulting in `chat.message` frames and TTS events via the engine’s streaming controller (not modified here).【F:app/ws/adapter.py†L3270-L3336】【F:app/voice_v2/engine.py†L976-L1078】
- TTS streaming: The adapter converts engine TTS events to WebSocket `tts.start`/`tts.end` plus streamed audio chunks, tracking utterance IDs and metadata for greet detection and telemetry.【F:app/ws/adapter.py†L1275-L1333】【F:app/ws/adapter.py†L1341-L1497】

### 2.4 How the Server Uses Phases / State (If at All)
- The adapter tracks greet state (`greet_completed`, `greet_utt_id`), ASR readiness (`asr_ready_bundle_sent_ms`, `session.asr_state`), mic flags (`client_capture_armed`, `accepting_audio`), and TTS activity (`session.tts_active`). These flags gate ASR open and audio acceptance but there is no explicit phase enum mirroring the client PHASE model.【F:app/ws/adapter.py†L1220-L1267】【F:app/ws/adapter.py†L1341-L1497】【F:app/ws/adapter.py†L3953-L4052】
- The engine maintains turn state (READY/LISTENING/THINKING/RESPONDING) per session but does not expose a PHASE.Greet/ConversationReady concept to the adapter/client beyond greet markers.【F:app/voice_v2/engine.py†L80-L120】【F:app/voice_v2/engine.py†L946-L1078】

### 2.5 How Mic Input is Treated During Greet (Server’s View)
- Incoming audio before greet completion is rejected at two layers:
  - `asr.open` requests from the client are ignored until `greet_completed` is set by a TTS end event.【F:app/ws/adapter.py†L3346-L3355】
  - `_ensure_asr_ready` is a no-op before greet, preventing automatic ASR ready signals/opening.【F:app/ws/adapter.py†L1220-L1241】
- If audio arrives while ASR is not ready or accepting, `_handle_binary` responds with `audio_not_expected` errors and does not feed PCM into ASR, effectively treating pre-greet mic input as invalid rather than buffered.【F:app/ws/adapter.py†L3953-L4052】
- Audio returned to the client is limited to TTS streams; there is no server echo of client PCM in normal operation (non-TTS audio frames are filtered by `_extract_outbound_payload`).【F:app/ws/adapter.py†L4900-L5020】

## 3. Target / Recommended Server Design

### 3.1 Align Server Behavior with Client Phase Model
- **PHASE.Greet (output-only):** Keep ASR closed/ignored until greet TTS completes. All greet TTS frames should carry `meta.is_greet` and emit `greet.start`/`greet.complete` so the client can stay in Greet and transition to ConversationReady when greet completes. Map to adapter greet handlers and engine greet generation.
- **PHASE.ConversationReady:** After `greet.complete` (or `tts.end` with `meta.is_greet`), auto-send `asr.ready` + `input.start` (or equivalent) and allow `_ensure_asr_ready` to open ASR. Hook into `_handle_tts_end` and `_ensure_asr_ready`.
- **Conversation turns:** Continue using the existing ASR → LLM → TTS pipeline; ensure utterance IDs/req_ids are propagated consistently for telemetry and client lane separation.

### 3.2 Single Audio Contract
- All synthesized audio (greet + conversation) should follow `tts.start` → audio chunks → `tts.end` with consistent metadata, including `meta.is_greet` for greet utterances and utterance IDs for correlation (handled in `_handle_tts_start/_end`).
- The server should never send client PCM back as playback audio; any diagnostic audio lanes should remain disabled or clearly typed for clients to ignore.

### 3.3 ASR / LLM Guardrails Around Greet
- While greet is active, keep ASR closed and drop/ignore PCM without side effects; do not arm ASR ready deadlines until greet completion. Use `greet_completed` and `session.tts_active` checks in `_ensure_asr_ready` and `_handle_binary` to gate ingestion.
- When greet ends, explicitly arm ASR open + ready emission and notify the engine to enable full duplex, aligning with client PHASE.ConversationReady.

### 3.4 Concrete Recommended Changes (Do Not Implement Yet)
- **[Greet Mode]** Enforce `meta.is_greet` tagging for all greet TTS frames and maintain `greet_utt_id` across vendors; ensure `_handle_tts_end` always emits `greet.complete` and calls `_ensure_asr_ready` immediately after greet.
- **[Conversation Mode]** After greet completion, explicitly transition to “conversation ready” by arming ASR and sending any required `input.start`/`asr.ready` bundle; ensure `enable_full_duplex` is called once per greet completion.
- **[Audio Discipline]** Keep PCM echo paths disabled; if any debug audio streams exist, keep them out of `_extract_outbound_payload` for production clients.

## 4. Gaps & Mismatches Between Server and Client
- Client expects a clear PHASE.ConversationReady signal; server today relies on `greet.complete` + implicit `asr.ready` but does not emit an explicit phase change event.
- Greet metadata depends on vendor markers/fallbacks; `meta.is_greet` may be missing if TTS provider omits markers and fallback tracking is not triggered.
- ASR gating is implemented, but any PCM sent during greet results in errors rather than being buffered/ignored gracefully, which may differ from client expectations for muted mic during greet.
- No explicit server phase enum; state is implicit across multiple flags, making cross-layer alignment with the client phase model harder to reason about.

## 5. Implementation Plan Overview (Future Work)
- Normalize greet signaling: ensure all greet utterances set `meta.is_greet`, always emit `greet.start`/`greet.complete`, and document them as the trigger for PHASE transitions.
- Add ASR gating polish: keep `_ensure_asr_ready` idle until greet completion, then immediately arm ASR open/ready and send a clear ConversationReady cue (e.g., a dedicated frame or enriched `greet.complete`).
- Tighten audio contracts: audit outbound payload extraction to guarantee only TTS playback audio is streamed; keep any diagnostic audio disabled by default.
- Add telemetry/observability: temporary logs or counters confirming greet vs conversation modes, ASR gating decisions, and phase-aligned events.
