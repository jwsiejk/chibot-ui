BUILD 04 — Gate Model, TTS Mask, and Turn State Machine — Task B4-A: Mic Gate Reason Model

Alignment guard (do not omit):

Align with SSOT in /docs (00_CONTEXT.md, 10_CONTRACT_WS.md, 20_ARCH_BUILD_ORDER.md).
Touch only the files listed below.
Do not rename routes, env vars, or policy keys.
Keep each new/changed file ≤ 500 lines.
Preserve the chat.v2 contract and telemetry envelope v1 (server fills ts_ms, level; meta redaction applies).
This task does not wire into TTS or turns yet (that’s B4-B/C).

Goal

Create a small, deterministic GateController that tracks mic mask truth from multiple reasons and emits a single effective state with a telemetry breadcrumb on changes. Reasons:

tts_active — assistant audio is playing

manual_gate — deliberate user hold-to-talk

system_hold — short post-TTS hold to avoid echo

Files to create/modify

app/voice_v2/gate.py (new)

tests/test_gate_controller.py (new)

scripts/run_build04_tests.sh (new or update if it exists; include this test)

Do not modify any other files.

Non-goals (explicit)

No engine/adapter wiring in this task.

No timers; post-hold timing comes in B4-B.

No policy decisions; just the gate model + publishes.

Requirements

Data model & API

Implement in app/voice_v2/gate.py:

class GateController:
    """
    Tracks reasoned mic mask state.
    Reasons: {"tts_active", "manual_gate", "system_hold"} -> bool
    Effective: True = masked/closed, False = open.
    """

    def __init__(self, publish):
        """
        publish: Callable[[dict], None]
        A function that accepts a telemetry event envelope (v1).
        """

    def set_reason(self, reason: str, on: bool, *, sid: str | None = None, meta: dict | None = None) -> None: ...

    def clear_all(self, *, sid: str | None = None) -> None: ...

    def snapshot(self) -> dict:
        """Return {"reasons": {...}, "effective": bool}"""


Effective = any(self._reasons.values()).

Supported reasons: exactly {"tts_active","manual_gate","system_hold"}; unknown reason → no-op.

Telemetry publish (envelope v1)

On any semantic change (a reason flips or effective changes), call publish({...}) with:

{
  "type": "EVT_MIC_GATE",
  "sid": "<sid if provided>",
  "level": "debug",
  "meta": {
    "gate": {
      "state": "on" | "off",
      "mask": true | false,
      "reason": "<reason or 'multi'>",
      "reasons": { "tts_active": true|false, "manual_gate": true|false, "system_hold": true|false }
    }
  }
}


state:"on" ↔ mask:true when effective is masked.

If multiple reasons are true, set reason:"multi".

Behavioral constraints

Idempotent: setting a reason to its current value must not republish.

clear_all() turns all reasons off in one step; publishes if effective flips.

No sleeps/threads; no global imports. publish is injected.

Code quality

Type hints on public methods.

Tiny docstrings.

Module ≤ 300 LOC.

Acceptance (must pass)

set_reason("tts_active", True) publishes one EVT_MIC_GATE with meta.gate.state=="on" and mask==True.

Clearing the same reason publishes one event with state:"off", mask==False (assuming no other reasons).

set_reason(..., same value) does not publish.

snapshot() returns {"reasons":{...},"effective":<bool>} reflecting current truth.

Smoke Tests (stdlib unittest)

Files: tests/test_gate_controller.py, scripts/run_build04_tests.sh

tests/test_gate_controller.py

test_set_clear_reasons_and_effective

Construct controller with a publish stub that appends envelopes to a list.

set_reason("tts_active", True) → one event, state:"on", mask:true.

set_reason("tts_active", False) → one additional event, state:"off", mask:false.

snapshot()["effective"] is False.

test_idempotent_no_duplicate_publish

Two consecutive set_reason("manual_gate", True) calls produce one publish.

test_multi_reason_marks_multi

set_reason("manual_gate", True) then set_reason("system_hold", True) → last event has reason:"multi" and mask:true.

scripts/run_build04_tests.sh (create or append)

#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}:."
PY="${PYTHON:=python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -m unittest -v tests.test_gate_controller
echo "BUILD_04_TESTS: PASS"

Deliverables

app/voice_v2/gate.py (new)

tests/test_gate_controller.py (new)

scripts/run_build04_tests.sh (new or updated to include this test)

Return only the diffs for the files listed above. Do not modify or create any other files.