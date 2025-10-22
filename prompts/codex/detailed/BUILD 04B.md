BUILD 04 — Gate Model, TTS Mask, and Turn State Machine — Task B4-B: TTS Mask Lifecycle Hooks

Alignment guard (do not omit):

Align with SSOT in /docs (00_CONTEXT.md, 10_CONTRACT_WS.md, 20_ARCH_BUILD_ORDER.md).
Touch only the files listed below.
Do not rename routes, env vars, or policy keys.
Keep each new/changed file ≤ 500 lines.
Preserve the chat.v2 contract and telemetry envelope v1 (server fills ts_ms, level; meta redaction applies).
Assume B4-A GateController exists; wire it here.

Goal

Wire the mic gate to the TTS playback lifecycle so that:

When assistant starts speaking, tts_active is set ON.

When the utterance ends, tts_active is cleared and a short post-hold (system_hold) engages for post_hold_ms, then releases.

Each transition emits the correct telemetry breadcrumbs.

Files to create/modify

app/voice_v2/engine.py (update)

tests/test_tts_mask_lifecycle.py (new)

scripts/run_build04_tests.sh (update to include this test)

Do not modify any other files.

Non-goals (explicit)

No vendor changes; no audio streaming code here.

No UI updates.

No state machine transitions yet (B4-C handles that).

Requirements

Engine wiring (construct + helper)

Ensure the engine constructs a GateController (from B4-A), injecting the engine’s telemetry publisher for publish.

Add:

def on_tts_start(self, sid: str, utt_id: str, post_hold_ms: int | None = None) -> None: ...
def on_tts_end(self, sid: str, utt_id: str, post_hold_ms: int | None = None) -> None: ...


Behavior on TTS start

on_tts_start(...):

gate.set_reason("tts_active", True, sid=sid, meta={"tts":{"utt_id": utt_id, "post_hold_ms": post_hold_ms or 0}})

Publish (or confirm publish) of EVT_TTS_START with meta.tts.utt_id and meta.tts.post_hold_ms.

Behavior on TTS end

on_tts_end(...):

gate.set_reason("tts_active", False, sid=sid, meta={"tts":{"utt_id": utt_id}})

If post_hold_ms and post_hold_ms > 0:

gate.set_reason("system_hold", True, sid=sid, meta={"tts":{"utt_id": utt_id, "post_hold_ms": post_hold_ms}})

Schedule a non-blocking release after post_hold_ms:

Use asyncio.create_task(self._release_system_hold_after(sid, post_hold_ms))

_release_system_hold_after calls gate.set_reason("system_hold", False, sid=sid)

Publish (or confirm publish) of EVT_TTS_END with meta.tts.utt_id.

Quality constraints

No blocking sleeps; only async scheduling or equivalent non-blocking pattern.

Idempotency: repeated on_tts_start or on_tts_end must not spam the same state if unchanged.

≤ 250 LOC delta.

Acceptance (must pass)

Calling on_tts_start(sid, "u1", post_hold_ms=200) sets tts_active and emits EVT_MIC_GATE (on), plus EVT_TTS_START.

Calling on_tts_end(sid, "u1", post_hold_ms=200) clears tts_active, sets system_hold ON, emits gate transition(s), and later releases system_hold (OFF).

With post_hold_ms=0, system_hold is not engaged; gate returns to open after clearing tts_active.

Smoke Tests (stdlib unittest)

Files: tests/test_tts_mask_lifecycle.py, scripts/run_build04_tests.sh (update)

test_tts_start_end_with_post_hold

Arrange: instantiate engine with a fake exporter/publisher; hook bus subscribers to capture EVT_MIC_GATE and EVT_TTS_*.

Act: on_tts_start(..., post_hold_ms=200) then on_tts_end(..., post_hold_ms=200).

Assert: sequence shows gate on at start, then off only after post-hold release; both EVT_TTS_START/EVT_TTS_END present with utt_id.

test_tts_end_no_post_hold

Act: on_tts_start(..., post_hold_ms=0), on_tts_end(..., post_hold_ms=0).

Assert: system_hold never engaged; gate clears immediately.

Runner update (append to existing file):

#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}:."
PY="${PYTHON:=python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -m unittest -v \
  tests.test_gate_controller \
  tests.test_tts_mask_lifecycle
echo "BUILD_04_TESTS: PASS"

Deliverables

app/voice_v2/engine.py (updated)

tests/test_tts_mask_lifecycle.py (new)

scripts/run_build04_tests.sh (updated)

Return only the diffs for the files listed above. Do not modify or create any other files.