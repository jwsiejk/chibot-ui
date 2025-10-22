
---

### `docs/20_ARCH_BUILD_ORDER.md`

```markdown
# Architecture Build Order (v2)

> Build sizes are **Codex-friendly** (≤3 files / task, ≤300–500 lines).  
> Every task has explicit **Non-goals** and **Acceptance**.

## Build 1 — Server v2 Bootstrap
- **B1-A: Telemetry Bus + events**  
  Files: `app/telemetry/bus.py`, `app/voice_v2/__init__.py`  
  Acceptance: subscribe/publish, wildcard `'*'`, safe handler exceptions.
- **B1-B: WS Adapter**  
  Files: `app/ws/adapter.py`, `app/asgi_gateway.py`  
  Acceptance: require subprotocol `chat.v2`; ping→pong; clean close; `/api/v1/health` shows `engine:"v2"`.
- **B1-C: Engine shell + minimal exporter**  
  Files: `app/voice_v2/engine.py`, `app/telemetry/exporter.py`  
  Acceptance: EVT_WS_* taps recorded under `exports/<sid>/…`.

## Build 2 — Policy Manager (+ telemetry block)
- **B2-A:** `app/policy/loader.py` adds defaults incl. `telemetry{…}`; doc synced.  
  Acceptance: snapshot has 4 core keys + telemetry.
- **B2-B:** `app/voice_v2/policy_manager.py` (precedence = policy_state ∧ admin_switch).  
  Acceptance: diff(prev→next) only changed keys.
- **B2-C:** `engine.py` pushes full `policy.interaction` on open; exporter logs diffs.  
  Acceptance: one `policy:applied` with diffs.
- **B2-D:** Breadcrumb `acwr_recompute {policy_acwr, admin_enabled} -> {effective}`.

## Build 3 — TTS Tracker
- **B3-A:** `voice_v2/tts_tracker.py` (start/end/is_active/release_at_ms).
- **B3-B:** Engine integration → `tts.start … (seconds) … tts.end`; post-hold → idle w/ acwr:true.
- **B3-C:** Exporter subscribes to `EVT_TTS_*` (log `utt_id`, `post_hold_ms`).

## Build 4 — Gate & Barge (auto only)
- **B4-A:** `voice_v2/gate_controller.py` (gate on/off reasons; respects telemetry).
- **B4-B:** Engine honors `barge_in_enabled` during TTS (ignore vs interrupt).
- **B4-C:** `EVT_BARGE_IN {source:"auto_vad|asr_evidence", granted:true|false}`.
- **B4-D:** Auto Barge Event Schema & Decision

## Build 5 — ASR Manager + NLU/NLG seams
- **B5-A:** `voice_v2/asr_manager.py` (Deepgram first): warm_up → `EVT_ASR_READY`; partial/final with `req_id`.
- **B5-B:** Add Speechmatics adapter + selection; identical event shapes.
- **B5-C:** NLU hook: after `asr.final`, log exactly one NLU per turn.
- **B5-D:** Dialog Policy + NLG hook: log decision + exactly one NLG per turn.
  **B5-E:** Audio Envelope & Jitter Buffer
  **B5-F:** Provider Interfaces & Circuit Breakers

## Build 6 — Telemetry Exporter (full)
- **B6-A:** Bundle structure & redaction (manifest, server log, ws taps, flow timeline).
- **B6-B:** Levels/categories/sampling → policy toggles apply immediately.
- **B6-C:** Provider debug channels (IDs + timings, never secrets).'
- **B6-D:** Admin Flow Trace API
  **B6-E:** Performance Telemetry & Budgets
  **B6-F:** Local Tests & Runner (Exporter/Zip/Perf API)

## Build 7 — Client v2 Minimal
- **C7-A:** WS layer + PolicyBus (ACWR stickiness if omitted).
- **C7-B:** Waveform + state badges (reflect policy & TTS in real time).
- **C7-C:** Playback truth + auto barge (onplay/onended; client telemetry).
- **C7-D:** Recorder + sender (format header; partial/final round-trip).
- **C7-E:** Recorder Contract & StartOnce
- **C7-F:** Reconnect UX + Resume
- **B7-E:** Policy Inspector (Read-only)

## Build 8 — Cutover & Guards
- **B8-A:** Single-path v2: `/ws/v1/chat` returns 410; `/ws/v2/chat` only.
- **B8-B:** CI checks doc/script — fail PR if v1 path or top-level `templates/` appears.

### NLU/NLG wiring (when)
- Wire NLU after **Build 5** (`asr.final`); log one NLU/turn.
- Add NLG mini-build in **Build 5**; log one NLG/turn.
- Exporter captures both in their own streams.
