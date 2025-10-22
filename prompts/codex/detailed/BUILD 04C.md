BUILD 04 — Gate Model, TTS Mask, and Turn State Machine — Task B4-C: Engine Turn State Machine

Alignment guard (do not omit):

Align with SSOT in /docs (00_CONTEXT.md, 10_CONTRACT_WS.md, 20_ARCH_BUILD_ORDER.md).
Touch only the files listed below.
Do not rename routes, env vars, or policy keys.
Keep each new/changed file ≤ 500 lines.
Preserve the chat.v2 contract and telemetry envelope v1 (server fills ts_ms, level; meta redaction applies).
Assume B4-A/B are complete; wire turn transitions here.

Goal

Introduce a robust yet simple turn state machine with observable breadcrumbs and timing:

States: Ready → Listening → Thinking → Responding → Ready

Ready — idle; mic may open (policy dependent)

Listening — user audio is being captured (first inbound audio of a turn)

Thinking — ASR final observed; LLM/NLG pending or underway

Responding — assistant speaking (TTS start..end)

Return to Ready ends the turn and records duration

Files to create/modify

app/voice_v2/engine.py (update)

tests/test_turn_state_machine.py (new)

scripts/run_build04_tests.sh (update to include this test)

Do not modify any other files.

Non-goals (explicit)

No vendor hooks or adapter changes.

No strict timeout policy (emit EVT_TIMEOUT optionally, but budget enforcement is out of scope).

Requirements

State enum & storage

Define constants or an Enum: READY, LISTENING, THINKING, RESPONDING.

Store per-sid: state, turn_id, turn_started_ms.

Transition helper

Add a private helper:

def _set_state(self, sid: str, new_state: str, *, reason: str | None = None) -> None:
    """
    Idempotent; no-op if same state.
    Emits EVT_TURN_BEGIN on entering LISTENING with new turn_id.
    Emits EVT_TURN_END on entering READY from RESPONDING/THINKING/LISTENING with duration_ms.
    Publishes a small breadcrumb for each change with meta.state and optional reason.
    """


On entering LISTENING: generate turn_id (uuid4 or monotonic int), set turn_started_ms = now(), publish EVT_TURN_BEGIN with meta={"turn_id":..., "state":"Listening"}.

On returning to READY: compute duration_ms = now() - turn_started_ms, publish EVT_TURN_END with meta={"turn_id":..., "duration_ms":...}; clear turn_id and turn_started_ms.

Engine entry points → states

on_open(...) → READY.

First on_audio(sid, ..., seq) of a turn → _set_state(sid, LISTENING, reason="audio_rx").

A stub method on_asr_final(sid, text: str) (added here for tests) → _set_state(sid, THINKING, reason="asr_final").

on_tts_start(...) (from B4-B) → _set_state(sid, RESPONDING, reason="tts_start").

on_tts_end(...) (B4-B) → _set_state(sid, READY, reason="tts_end") after any post-hold unmask completes.

Telemetry envelope v1

All published events use the v1 envelope; server fills ts_ms, level; meta redaction applies by the bus.

Include sid where available.

Code quality

Idempotent transitions; no duplicate emits on same → same.

≤ 350 LOC delta; tiny docstrings.

Acceptance (must pass)

Simulated flow: on_open(sid) → first on_audio(sid, b"...", seq=1) → on_asr_final(sid,"hi") → on_tts_start(sid,"u1") → on_tts_end(sid,"u1")

Publishes (in order):

EVT_TURN_BEGIN when entering Listening

EVT_TURN_END when returning to Ready

turn_id is consistent across the turn; duration_ms > 0.

Re-entering Listening later creates a new turn_id.

Smoke Tests (stdlib unittest)

Files: tests/test_turn_state_machine.py, scripts/run_build04_tests.sh (update)

test_basic_turn_flow

Arrange: engine instance with fake exporter, bus subscriber capturing events.

Act: on_open → first on_audio → on_asr_final("hi") → on_tts_start("u1") → on_tts_end("u1").

Assert: EVT_TURN_BEGIN then EVT_TURN_END; verify same turn_id and positive duration_ms.

test_new_turn_id_on_second_listening

Act: start a second cycle; first on_audio after Ready.

Assert: new turn_id different from prior one.

Runner update (append):

#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}:."
PY="${PYTHON:=python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -m unittest -v \
  tests.test_gate_controller \
  tests.test_tts_mask_lifecycle \
  tests.test_turn_state_machine
echo "BUILD_04_TESTS: PASS"

Deliverables

app/voice_v2/engine.py (updated)

tests/test_turn_state_machine.py (new)

scripts/run_build04_tests.sh (updated)

Return only the diffs for the files listed above. Do not modify or create any other files.