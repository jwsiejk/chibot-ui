# AskChip Voice Turn Pipeline Debug Guide

## 1. Overview: Greet vs Conversation Turns

AskChip treats the initial greet as a system-only **turn 0**, followed by user/assistant pairs numbered from `turn_index = 1` onward:

1. **Greet (turn 0):** Chip speaks the greeting; no user audio is expected yet.
2. **User answers greet (turn 1 user):** First real user speech.
3. **Chip responds (turn 1 assistant):** Reply to the user’s answer, still part of turn 1.
4. **User follow-up (turn 2 user)** and **Chip response (turn 2 assistant)** continue the sequence for subsequent turns.

Turn tracking is enforced server-side in the WebSocket adapter and TurnEngine. The greet-specific metrics initializers in `_ensure_greet_turn` seed `turn_index = 0` before any TTS begins, while `_next_turn_index` (see adapter code below) advances the counter when ASR is opened for the first real user turn.

---

## 2. Client-Side Voice Pipeline

### 2.1 Core JS entrypoints (`app/static/js/ws_client.js`)

The WebSocket client wires transcript delivery and handles voice frames:

```javascript
// app/static/js/ws_client.js (createTranscriptBridge wiring)
  const transcriptBridge = createTranscriptBridge({
    AppState,
    hubLog: logStage,
    logStage,
    dispatchFrame,
  });

  const {
    deliverAsr,
    deliverChat,
    handleChatHistoryFrame,
    transcriptFrameAllowed,
    attachTranscriptView,
    handleAssistantStreamingBegin,
    handleAssistantStreamingDelta,
    handleAssistantStreamingCommit,
    handleAssistantStreamingEnd,
  } = transcriptBridge || {};

  let deliverUserTurn = null;

  if (transcriptBridge && typeof transcriptBridge.deliverUserTurn === "function") {
    // Normal path: use the bridge implementation.
    deliverUserTurn = (frame) => transcriptBridge.deliverUserTurn(frame);
  } else {
    // Fallback: synthesize a chat.message so the user still sees their bubble.
    deliverUserTurn = (frame) => {
      if (!frame || typeof frame !== "object") return;
      const text = typeof frame.text === "string" ? frame.text : "";
      if (!text.trim()) return;

      const chatFrame = {
        type: "chat.message",
        role: "user",
        text,
        req_id: typeof frame.req_id === "string" ? frame.req_id : undefined,
        turn_id: typeof frame.turn_id === "string" ? frame.turn_id : undefined,
        turn_index: typeof frame.turn_index === "number" ? frame.turn_index : undefined,
        ts: typeof frame.ts === "number" ? frame.ts : undefined,
      };

      if (transcriptFrameAllowed && transcriptFrameAllowed(chatFrame)) {
        try {
          if (typeof deliverChat === "function") {
            deliverChat(chatFrame);
          }
        } catch (err) {
          console.warn("user.turn fallback deliverChat error", err);
        }
      }
    };
  }
```

The handler dispatches inbound frames and routes ASR/user events to the transcript bridge:

```javascript
// app/static/js/ws_client.js (handleMessageFrame excerpts)
    switch (frame.type) {
      ...
      case "asr.partial":
        schedulePartialWatchdog("asr.partial");
        if (transcriptFrameAllowed(frame)) {
          deliverAsr(frame);
        } else {
          logStage("ui_transcript_filter", { allow: false, type: frame.type });
        }
        handledByTranscriptDispatch = true;
        break;

      case "user.turn":
        try { deliverUserTurn && deliverUserTurn(frame); } catch (e) { console.warn("user.turn err", e); }
        handledByTranscriptDispatch = true;
        break;

      case "asr.final":
        clearPartialWatchdog();
        try {
          const finalText = frame?.text ?? frame?.transcript ?? frame?.final ?? null;
          logStage("client.asr", {
            stage: "final",
            text: typeof finalText === "string" ? finalText : null,
            isPartial: false,
          });
        } catch (_) {}
        if (transcriptFrameAllowed(frame)) {
          deliverAsr(frame);
        } else {
          logStage("ui_transcript_filter", { allow: false, type: frame.type });
        }
        handledByTranscriptDispatch = true;
        break;
```

* **`user.turn`** frames render user chat bubbles via `deliverUserTurn` (canonical path).
* **`asr.partial`/`asr.final`** feed ASR text into the transcript UI without creating user chat bubbles.

### 2.2 Audio runtime & gates (`app/static/js/audio/ws_audio_runtime.js`)

Soft gate is now telemetry-only; hard gate is enforced before sending PCM over WS:

```javascript
// app/static/js/audio/ws_audio_runtime.js (soft gate & telemetry)
  function shouldSendFrameSoftGate({ vadState, speechSeen }) {
    // Soft gate is telemetry-only for the AskChip mic lane; ASR/TurnEngine decide
    // end-of-speech and turn boundaries instead of client-side VAD drops.
    if (!vadState) {
      return { shouldSend: true, vadLikelySpeech: false, rmsAtTrigger: null, reason: "no_vad_state" };
    }
    const state = typeof vadState?.state === "string" ? vadState.state : null;
    const vadLikelySpeech = Boolean(
      vadState?.isSpeech ||
      vadState?.speech ||
      vadState?.speaking ||
      state === "speech" ||
      state === "voice" ||
      (state && state !== "silence" && state !== "quiet")
    );
    if (!speechSeen && vadLikelySpeech) {
      speechStartSeen = true;
    }
    const shouldSend = vadLikelySpeech;
    const reason = vadLikelySpeech ? "vad_speech" : "vad_silence";
    const rmsAtTrigger = !speechSeen && vadLikelySpeech ? vadState?.rms ?? vadState?.rmsDb ?? null : null;
    return { shouldSend, vadLikelySpeech, rmsAtTrigger, reason };
  }

  function maybeEmitSoftGateTelemetry({ reason, vadLikelySpeech, wsPhase, appPhase, wsReadyState }) {
    const safeReason = reason || "unknown";
    const reasonChanged = safeReason !== lastSoftGateTelemetryReason;
    softGateTelemetryFrameCounter += 1;
    const intervalHit = softGateTelemetryFrameCounter >= softGateTelemetryIntervalFrames;
    if (!reasonChanged && !intervalHit) {
      return;
    }
    lastSoftGateTelemetryReason = safeReason;
    softGateTelemetryFrameCounter = 0;
    emitPolicyHook("soft_gate_telemetry", {
      reason: safeReason,
      allowed: true,
      vadLikelySpeech: Boolean(vadLikelySpeech),
      wsPhase,
      appPhase,
      wsReadyState,
    });
  }
```

When VAD first sees speech, the client marks the turn and emits `client.turn_start`, gathers preroll PCM, and sends it twice (“double tap”) to avoid race conditions:

```javascript
// app/static/js/audio/ws_audio_runtime.js (turn start + preroll + hard gate send)
    const softDecision = isKeepalive
      ? { shouldSend: true, vadLikelySpeech: false, rmsAtTrigger: null, reason: "keepalive" }
      : shouldSendFrameSoftGate({ vadState, speechSeen: speechSeenThisTurn });

    if (!isKeepalive && !speechSeenThisTurn && softDecision.vadLikelySpeech) {
      markSpeechSeen({ rmsAtTrigger: softDecision.rmsAtTrigger, framesSinceGreet: null, reqId: currentReqId });
      const turnIdCandidate = typeof getCurrentTurnReqId === "function" ? getCurrentTurnReqId() : null;
      currentTurnId = turnIdCandidate && `${turnIdCandidate}`.length ? `${turnIdCandidate}` : allocateTurnId();
      // 1. START: Wake up the server first
      try {
        safeSendJSON({
          type: "client.turn_start",
          lane: "mic",
          turn_id: currentTurnId,
          pre_roll_ms: 0,
        });
      } catch (_) {}
      // 2. PREPARE AUDIO
      try {
        prerollChunksToSend = ringBufferManager.drainAll();
      } catch (_) {
        prerollChunksToSend = [];
      }
      // 3. AUDIO "DOUBLE TAP": Send now, and send again shortly to beat the race condition
      if (prerollChunksToSend && prerollChunksToSend.length) {
        const prerollRate = meta?.sampleRate || meta?.sampleRateHz || asrRate;
        const seq = pcmLastSeq;

        // Burst 1: Immediate (might be dropped by race condition)
        sendPrerollChunks(prerollChunksToSend, prerollRate, { turnId: currentTurnId, seq });
        // Burst 2: Delayed (guaranteed to arrive after server is armed)
        setTimeout(() => {
          if (currentTurnId) { // Only send if turn still active
            sendPrerollChunks(prerollChunksToSend, prerollRate, { turnId: currentTurnId, seq });
          }
        }, 50);

        prerollChunksToSend = null; // Clear so we don't triple-send below
      }
      try {
        logStage("client.google_v3.turn_sequence_initiated", { turnId: currentTurnId });
      } catch (_) {}
    }
    // Telemetry-only soft gate: we always send PCM when the hard gate allows it.
    maybeEmitSoftGateTelemetry({
      reason: softDecision.reason || "unknown",
      vadLikelySpeech: Boolean(softDecision.vadLikelySpeech),
      wsPhase,
      appPhase: phaseValue,
      wsReadyState,
    });

    // ✅ NEW: actually send the PCM over the WebSocket.
    const sent = safeSendAudioChunk(chunk, {
      lane: "mic",
      sampleRateHz: effectiveSampleRate,
      chunkCount,
      seq,
      keepalive: isKeepalive,
      turnId: currentTurnId,
    });
```

* Hard gate (`computeHardGateSnapshot`) must allow the send; otherwise frames drop.
* Soft gate no longer blocks audio—so turn 1/turn 2 rely on server ASR to set boundaries.

### 2.3 Transcript bridge & `user.turn` (`app/static/js/ws/transcript_bridge.js`)

`createTranscriptBridge` keeps ASR partials separate from user bubbles and treats `user.turn` as canonical:

```javascript
// app/static/js/ws/transcript_bridge.js (deliverAsr + deliverUserTurn)
  function deliverAsr(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    if (frame.type === "asr.final" && typeof frame.sid === "string" && frame.sid) {
      if (AppState.asrSid && AppState.asrSid !== frame.sid) {
        console.warn("asr.final sid mismatch", { expected: AppState.asrSid, sid: frame.sid });
      } else {
        AppState.asrSid = frame.sid;
      }
    }
    const view = window.TranscriptView;
    const now = Date.now();
    if (frame.type === "asr.final" && typeof frame.text === "string" && frame.text) {
      const sid = (typeof frame.sid === "string" && frame.sid) || generateProvisionalSid();
      lastUserBySid.set(sid, { text: frame.text, ts: now });
      pruneStaleUserSids(now);
      const reqId = typeof frame.req_id === "string" ? frame.req_id : typeof frame.reqId === "string" ? frame.reqId : null;
      const turnId = typeof frame.turn_id === "string" ? frame.turn_id : null;
      const key = `${reqId || turnId || ""}|${frame.text}`;
      if (lastAsrFinalKey === key) {
        return;
      }
      lastAsrFinalKey = key;
    } else {
      pruneStaleUserSids(now);
    }
    if (!view) {
      return;
    }
    try {
      if (frame.type === "asr.partial" && typeof view.handlePartial === "function") {
        view.handlePartial(frame);
      }
    } catch (err) {
      console.warn("TranscriptView ASR handler error", err);
    }
  }

  function deliverUserTurn(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    const text = typeof frame.text === "string" ? frame.text : "";
    if (!text.trim()) {
      try { logStage("ui_transcript_filter", { allow: false, type: "user.turn", reason: "empty_text" }); } catch {}
      return;
    }
    const reqId = typeof frame.req_id === "string" ? frame.req_id : typeof frame.reqId === "string" ? frame.reqId : null;
    const turnId = typeof frame.turn_id === "string" && frame.turn_id
      ? frame.turn_id
      : reqId;
    // Deduplicate user.turn frames defensively. Vendor finals can duplicate (server dedupes),
    // and the client also guards against back-to-back identical user.turn frames.
    const key = `${reqId || ""}|${text}`;
    if (lastUserTurnKey === key) {
      return;
    }
    lastUserTurnKey = key;

    const chatFrame = {
      type: "chat.message",
      role: "user",
      text,
      req_id: reqId || undefined,
      turn_id: turnId || undefined,
      turn_index: typeof frame.turn_index === "number" ? frame.turn_index : undefined,
      ts: typeof frame.ts === "number" ? frame.ts : undefined,
    };

    if (transcriptFrameAllowed(chatFrame)) {
      deliverChat(chatFrame);
    } else {
      logStage("ui_transcript_filter", { allow: false, type: "user.turn", role: "user" });
    }
  }
```

* `asr.partial` renders live text only; no user bubble.
* `user.turn` synthesizes a `chat.message` to render the bubble and carries `turn_index`/`turn_id` for correlation.

---

## 3. Server-Side Voice Pipeline

### 3.1 Adapter context & state (`app/ws/adapter.py`)

`AdapterContext` tracks per-connection state, including turn indices and ASR flags relevant to turn 1/turn 2:

```python
# app/ws/adapter.py (AdapterContext excerpts)
class AdapterContext:
    """Per-connection state."""

    turn_index: int = 0
    turn_req_id: Optional[str] = None
    previous_turn_req_id: Optional[str] = None
    active_req_id: Optional[str] = None
    ...
    asr_final_emitted: bool = False
    empty_final_count: int = 0
    ...
    audio_bridge_turn_index: Optional[int] = None
    bytes_from_client_this_turn: int = 0
    turn_audio_bytes: int = 0
    current_turn_id: Optional[str] = None
    current_turn_open: bool = False
    turn_start_ts_ms: Optional[int] = None
    auto_ready_probe_active: bool = False
    auto_ready_probe_promotion_logged: bool = False
```

Key meanings:
* `turn_index` / `session.turn_index`: monotonic turn counter (greet = 0, first user turn = 1, etc.).
* `current_turn_id` / `turn_req_id`: identifiers for the active turn; propagated to ASR and `user.turn`.
* `asr_final_emitted` / `empty_final_count`: track whether a non-empty ASR final has been seen and count empty finals.
* `audio_bridge_turn_index`: which turn the mailbox/jitter buffer is attributing PCM to.

### 3.2 WebSocket adapter: audio ingest & mailbox

Binary audio frames are always buffered (Conversation Core invariant) and logged with turn context:

```python
# app/ws/adapter.py (_handle_binary excerpt)
    async def _handle_binary(
        self, data: bytes, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
    ) -> _HandleResult:
        byte_count = len(data)
        ctx.last_client_activity_ms = int(time.time() * 1000)
        self._cancel_no_audio_watchdog(ctx)
        ...
        # Conversation Core INV-1: Mailbox / Always-Buffer Rule. For the primary
        # mic PCM lane we always accept binary audio, update ingress metrics,
        # and ingest into the bounded ring buffer. We never reject mic audio here
        # based on conversational state; turn/ASR decisions happen after
        # buffering.
        ctx.audio_violation_count = 0
        ...
        if ctx.auto_ready_probe_active:
            self._promote_probe_turn(ctx)
        ...
        if ctx.asr_stream_id is None:
            if ctx.current_turn_open and not ctx.asr_open and ctx.asr_open_task is None:
                await self._ensure_previous_turn_closed(ctx, "pcm_first_chunk")
                self._schedule_asr_open(ctx)
                self._log_audio_frame_ingest(
                    ctx,
                    "pre_open_asr_scheduled",
                    byte_count,
                )
                sample_rate = self._resolve_asr_sample_rate(ctx)
                ...
                await self._invoke_engine("on_asr_open", ctx.sid, ctx.current_turn_id)
            else:
                self._log_audio_frame_ingest(
                    ctx, "ignored_pre_open_no_stream", byte_count
                )
```

Per-frame logging includes turn and ASR state:

```python
# app/ws/adapter.py (_log_audio_frame_ingest)
    def _log_audio_frame_ingest(
        self,
        ctx: AdapterContext,
        decision: str,
        byte_count: int,
        note: str | None = None,
    ) -> None:
        if ctx.session.asr_state == "open" and not ctx.current_turn_id:
            ...
        meta = {
            "bytes": byte_count,
            "note": note,
            "asr_state": getattr(ctx.session, "asr_state", None),
            "turn_id": ctx.current_turn_id,
        }
        ...
            _log.info(
                "evt=google_v3.audio_frame_ingest sid=%s turn_id=%s decision=%s count=%d",
                ctx.sid,
                ctx.current_turn_id,
                decision,
                count,
                extra={"meta": meta},
            )
```

* `audio_bridge_turn_index` is set when `_start_audio_bridge_turn` runs (next section), aligning buffered audio with the current `turn_index`.

### 3.3 Turn lifecycle (most critical)

Starting a turn allocates req/turn IDs and resets per-turn counters:

```python
# app/ws/adapter.py (_start_user_turn)
    def _start_user_turn(self, ctx: AdapterContext) -> None:
        new_req_id = self._make_req_id(ctx)
        ctx.turn_req_id = new_req_id
        ctx.active_req_id = new_req_id
        ctx.current_turn_id = ctx.current_turn_id or new_req_id
        ctx.current_turn_open = True
        ctx.audio_meta = None
        ...
        ctx.turn_audio_bytes = 0
        ctx.turn_audio_chunks = 0
        ...
        ctx.auto_ready_probe_promotion_logged = False
        ctx.audio_turn_id_missing_logged = False
```

When ASR is scheduled/opened for a real turn, the adapter sets the `turn_index`, starts the audio bridge, and records lifecycle metrics (turn 1, turn 2, ...):

```python
# app/ws/adapter.py (_start_audio_bridge_turn)
    def _start_audio_bridge_turn(
        self,
        ctx: AdapterContext,
        turn_index: Optional[int] = None,
        profile: Optional[Mapping[str, Any]] = None,
    ) -> None:
        candidate_turn = turn_index
        if not isinstance(candidate_turn, int):
            try:
                candidate_turn = int(getattr(ctx, "turn_index", 0))
            except Exception:
                candidate_turn = None
        if not isinstance(candidate_turn, int) or candidate_turn <= 0:
            try:
                candidate_turn = int(getattr(ctx.session, "turn_index", 0) or 0)
            except Exception:
                candidate_turn = 0
        target_turn = max(1, candidate_turn)

        if ctx.audio_bridge_turn_index == target_turn:
            return

        ctx.audio_bridge_turn_index = target_turn
        ...
        _log.info(
            "evt=audio_bridge_turn_start sid=%s turn=%s codec=%s rate_hz=%s ch=%s",
            ctx.sid,
            target_turn,
            codec,
            rate_hz,
            channels,
        )
```

ASR scheduling (for probe vs real turns) resets per-stream flags and increments `turn_index` when not a probe:

```python
# app/ws/adapter.py (_schedule_asr_open)
    def _schedule_asr_open(self, ctx: AdapterContext, *, as_probe: bool = False) -> None:
        ...
        ctx.asr_final_emitted = False
        ctx.empty_final_count = 0
        ctx.asr_closed_ack_sent = False
        ctx.asr_result_seen = False
        ctx.asr_final_text = None
        ...
        self._start_user_turn(ctx)
        ctx.auto_ready_probe_active = as_probe
        mark(ctx.session, "opening")
        ...
        if not ctx.auto_ready_probe_active:
            turn_index = self._next_turn_index(ctx)
            now_ms = self._now_ms()
            TurnLifecycleRecorder.mark_turn_start(
                ctx, now_ms, ctx.current_turn_id, turn_index
            )
            self._start_audio_bridge_turn(ctx, turn_index)
            ...
            self._log_turn_event(
                ctx,
                "turn_start",
                asr_stream_id=getattr(ctx, "asr_stream_id", None),
            )
        ...
        try:
            ctx.asr_open_task = asyncio.create_task(self._open_asr(ctx))
```

Handling ASR results enforces non-empty finals, emits `user.turn`, and closes the turn:

```python
# app/ws/adapter.py (_handle_asr_result excerpts)
        if is_final:
            metrics["asr_final_at"] = time.monotonic()
            phase = "asr_final_from_timeout" if promoted_final else "asr_final"
            self._log_turn_event(
                ctx,
                phase,
                transcript=text,
                asr_stream_id=ctx.asr_stream_id,
                asr_req_id=getattr(ctx, "asr_stream_req_id", None),
            )
            TurnLifecycleRecorder.mark_asr_final(ctx, self._now_ms())
        ...
        if is_final:
            _log.info(
                "evt=asr_accepted_final sid=%s stream=%s state=%s text=%s",
                ctx.sid,
                ctx.asr_stream_id,
                getattr(ctx.session, "asr_state", None),
                (text[:120] if isinstance(text, str) else None),
            )
            ...
        stripped_text = text.strip()

        if is_final and stripped_text:
            ctx.asr_final_emitted = True
            ctx.empty_final_count = 0
        elif is_final and not stripped_text:
            ctx.empty_final_count += 1
            _log.info(
                "evt=asr_empty_final_ignored sid=%s turn_index=%s count=%s",
                ctx.sid,
                turn_index,
                ctx.empty_final_count,
            )
            ...
            if not timeout:
                return
        is_empty_final = bool(
            is_final
            and promoted_final
            and timeout
            and not stripped_text
        )
        is_real_user_final = (
            is_final
            and bool(stripped_text)
            and not timeout
            and not getattr(ctx, "diag_mode", False)
            and not getattr(ctx, "system_hold", False)
        )

        if not is_real_user_final:
            ...
            if not stripped_text:
                self._end_user_turn(ctx)
            return

        _log_llm_turn_decision("llm_turn", "non_empty_user_final")
        await self._emit_user_turn_event(ctx, text, turn_index, req_id_final)
        await self._invoke_engine("on_asr_final", ctx.sid, text, req_id_final)
        if ctx.session.asr_state == "open" or ctx.asr_open:
            await self._close_asr(ctx, reason="normal_final")
            _log.info(
                "evt=google_v3.asr_close",
                extra={
                    "sid": ctx.sid,
                    "turn_id": ctx.current_turn_id,
                    "bytes_from_client": ctx.bytes_from_client_this_turn,
                    "reason": "normal_final",
                },
            )
        ctx.current_turn_open = False
        ctx.current_turn_id = None
        ctx.turn_start_ts_ms = None
        ctx.bytes_from_client_this_turn = 0
        ...
        TurnLifecycleRecorder.finalize_and_log(ctx, outcome="ok")
        self._log_turn_summary(ctx, "ok")
        self._end_user_turn(ctx)
```

* Empty finals (no text) are ignored/telemetry unless a timeout forces a `turn.empty` frame; `turn_index` is used to label the outcome (affects turn 1/turn 2 debugging).
* `_emit_user_turn_event` sends the canonical `user.turn` frame with `turn_index`/`turn_id` for rendering on the client:

```python
# app/ws/adapter.py (_emit_user_turn_event)
    async def _emit_user_turn_event(
        self,
        ctx: AdapterContext,
        text: str,
        turn_index: Optional[int],
        req_id: Optional[str],
    ) -> None:
        turn_id = ctx.current_turn_id or req_id or ctx.turn_req_id
        text_value = text.strip() if isinstance(text, str) else ""
        if not text_value:
            _log.info(
                "evt=user_turn_event_skip_empty sid=%s turn_id=%s turn_index=%s",
                ctx.sid,
                turn_id,
                turn_index,
            )
            return
        ...
        payload = {
            "type": "user.turn",
            "sid": ctx.sid,
            "req_id": req_id,
            "turn_id": turn_id,
            "turn_index": turn_index,
            "text": text,
            "source": "asr",
        }
        _log.info(
            "evt=user_turn_event sid=%s turn_id=%s turn_index=%s", ctx.sid, turn_id, turn_index
        )
        if ctx.ws_send is not None:
            await self._send_json(ctx.ws_send, ctx.sid, payload)
```

### 3.4 GCP engine streaming loop (`app/services/asr/gcp_engine.py`)

The streaming worker iterates vendor responses and forwards transcripts to the adapter callback `_on_result`. `_handle_result` now marks finals only when text is non-empty:

```python
# app/services/asr/gcp_engine.py (_handle_result)
    def _handle_result(self, transcript: str, is_final: bool) -> None:
        if self._on_result is None:
            return

        stripped_transcript = transcript.strip()

        self._last_transcript = transcript
        self._last_is_final = bool(stripped_transcript) and is_final

        if is_final:
            logger.info(
                "evt=asr_final vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_final"},
            )
        else:
            logger.info(
                "evt=asr_partial vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_partial"},
            )

        if self._stats is not None:
            self._stats.mark_partial(transcript)

        try:
            maybe_coro = self._on_result(transcript, is_final)
            if asyncio.iscoroutine(maybe_coro):
                asyncio.create_task(maybe_coro)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "evt=asr_error vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_error"},
            )
```

* Empty finals set `_last_is_final = False`; the adapter ignores them unless timeout forces handling.
* `_on_result` is provided by `ChatV2Adapter`, so `_handle_asr_result` enforces empty-final behavior (turn 1/turn 2 alike).

### 3.5 EngineV2 and policy bridge (`app/voice_v2/engine.py`)

When the adapter emits a non-empty `asr.final`, EngineV2 bridges into policy/LLM/TTS:

```python
# app/voice_v2/engine.py (on_asr_final)
    def on_asr_final(self, sid: str, text: str, req_id: str | None = None) -> None:
        """Observe the final ASR transcript for a turn."""

        _log.info(
            "evt=voice.on_asr_final sid=%s req_id=%s text_preview=%s",
            sid,
            req_id,
            (text[:80] + "…") if isinstance(text, str) and len(text) > 80 else text,
        )

        session = self._ensure_session(sid)

        if session.state != LISTENING:
            _log.warning(
                "evt=asr_final_ignored state=%s", session.state, extra={"sid": sid}
            )
            return

        self._commit_turn_start(sid, "asr_final")
        session = self._ensure_session(sid)
        ...
        turn_index = getattr(session, "turn_index", 0)
        if turn_index >= 1:
            nlu_payload: Dict[str, Any] = {
                "req_id": req_id_value,
                "turn_id": session.turn_id
                if isinstance(session.turn_id, str) and session.turn_id
                else req_id_value,
                "intent": "chitchat.fallback",
                "entities": {},
                "text": text,
            }

            _log.info(
                "evt=voice.policy_bridge_start sid=%s req_id=%s turn_index=%s",
                sid,
                req_id_value,
                turn_index,
            )

            try:
                self._apply_policy_decision(sid, nlu_payload)
            except Exception:
                ...
```

* `turn_index` from the session drives downstream policy; turn 2 uses the same flow as turn 1 after greet.

---

## 4. End-to-End Turn Traces (Logs + Code Reference)

**Healthy first user turn (turn_index = 1):**
1. **Client mic** detects speech → `markSpeechSeen` emits `client.turn_start` before audio send (`ws_audio_runtime.js`).
2. PCM is sent twice with preroll; hard gate must be open (`ws_audio_runtime.js`).
3. **Server** receives PCM → `_handle_binary` buffers, possibly schedules `_schedule_asr_open` if not already open (`adapter.py`).
4. ASR stream opens → `audio_bridge_turn_start` logged for turn 1 (`_start_audio_bridge_turn`).
5. ASR partials → `_handle_asr_result` publishes `asr.partial` (adapter) → `deliverAsr` renders partials (client bridge).
6. ASR final with text → `_handle_asr_result` marks `asr_final_emitted`, emits `user.turn`, invokes `on_asr_final` (EngineV2), closes ASR, logs turn summary `outcome=ok`.
7. Client receives `user.turn` → `deliverUserTurn` renders user bubble; assistant response follows via chat streaming frames.

**Healthy second user turn (turn_index = 2):** Same as above, but `turn_index` increments in `_schedule_asr_open`; logs to expect:
* `client.turn_start` (mic) for turn 2.
* `audio_bridge_turn_start ... turn=2` (server).
* `google_v3.audio_frame_ingest ... decision=... turn_id=...` for turn 2 frames.
* `asr_open_begin` (adapter `_open_asr`) and subsequent partial/final logs.
* `asr_accepted_final` and `user_turn_event ... turn_index=2`.
* `TURN_METRICS` / `turn_summary outcome=ok` for turn 2.

If debugging “stuck after greet” or missing turn 2, verify the presence/order of these logs and whether empty finals short-circuit the `user.turn` path.

---

## 5. Known Invariants & Turn-2 Debug Checklist

**Invariants (Conversation Core relevant):**
* **Mailbox always buffers**: `_handle_binary` never rejects mic PCM based on state; turn gating happens after buffering.
* **Single TurnEngine owns lifecycle**: `_schedule_asr_open` + `_handle_asr_result` manage ASR open/close and emit `user.turn`.
* **Client gating is minimal**: Only hard gate stops PCM; soft gate is telemetry-only.
* **Timeouts are server-owned**: Empty finals/timeouts emit `turn.empty`; client does not enforce EOT.

**Turn 2 log/flow checks:**
* Did `client.turn_start` fire when the second user spoke? (see `markSpeechSeen` path).
* Did `_schedule_asr_open` run with `as_probe=False` (real turn) and bump `turn_index` to 2?
* Did `_start_audio_bridge_turn` log `audio_bridge_turn_start ... turn=2`?
* Did `asr_bytes_to_vendor_summary.bytes_from_bridge` for turn 2 show non-zero bytes?
* Did `_handle_asr_result` receive a non-empty final (or was it an empty final/timeout)?
* Did `_emit_user_turn_event` send `user.turn` with `turn_index=2`?
* Did EngineV2 log `voice.policy_bridge_start` for `turn_index=2`?

Collecting these breadcrumbs in a single trace helps isolate whether turn 2 is blocked at the client (no `client.turn_start`), at the adapter (no ASR open/bridge), or at ASR/LLM (empty finals or timeouts).
