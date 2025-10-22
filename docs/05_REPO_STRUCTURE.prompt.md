# Repo Structure (v2, synced)

This document reflects the **current** v2 layout. Use it as a guide for Codex build prompts and reviews.

```
repo/
  codex.patch
  app/
    asgi_gateway.py
    app/policy/
      loader.py
      watch.py
    app/security/
      auth.py
    app/telemetry/
      bus.py
      exporter.py
    app/voice_v2/
      __init__.py
      engine.py
      gate.py
    app/ws/
      adapter.py
      validator.py
  docs/
    00_CONTEXT.md
    05_REPO_STRUCTURE.prompt.md
    10_CONTRACT_WS.md
    15_NLU_NLG.md
    20_ARCH_BUILD_ORDER.md
    30_ADR.md
  ops/
    ENV_VARS.yml
    VERSION.yml
  prompts/
    prompts/chatgpt/
      SESSION_BOOTSTRAP.md
    prompts/codex/
      BUILD_01.md
      BUILD_02.md
      BUILD_03.md
      BUILD_04.md
      BUILD_05.md
      BUILD_06.md
      BUILD_07.md
      BUILD_08.md
      prompts/codex/detailed/
        BUILD 04A — Gate Model, TTS Mask, and Tur.md
        BUILD 04B.md
        BUILD 04C.md
  scripts/
    run_build01_tests.sh
    run_build02_tests.sh
    run_build03_tests.sh
    run_build04_tests.sh
  tests/
    conftest.py
    test_acwr_breadcrumb.py
    test_bus_publish_basics.py
    test_bus_redaction.py
    test_gate_controller.py
    test_policy_apply_and_diff.py
    test_policy_loader.py
    test_tts_mask_lifecycle.py
    test_turn_state_machine.py
    test_ws_binary_guard.py
    test_ws_json_contract.py
  doc/
    10_CONTRACT_WS.md
    20_ARCH_BUILD_ORDER.md
```

**Key v2 modules**
- `app/ws/adapter.py` — chat.v2 WebSocket adapter (bridge lives here).
- `app/voice_v2/engine.py` — Engine state machine + TTS/ASR hooks.
- `app/voice_v2/gate.py` — Gate controller and reasons.
- `app/policy/loader.py`, `app/policy/watch.py` — Interaction policy + diffs.
- `app/telemetry/bus.py` — Publish/subscribe with normalization (`schema_version:"1"`).
- `app/telemetry/exporter.py` — Session export taps.

**v2‑only**
- No legacy v1 routes, flags, or migration shims.

