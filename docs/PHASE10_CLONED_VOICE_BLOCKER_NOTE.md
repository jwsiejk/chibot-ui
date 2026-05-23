# Phase 10 Governance Note — standard voice active, cloned voice prerequisites pending

AskChappy is expected to run normally with the **standard voice** as the active/default path.

Cloned Chappy voice integration is **not yet configured** in this repository because the remaining governance prerequisites are still pending:
- Approved cloned-voice provider choice (provider/SDK/API contract).
- Approved local configuration shape for that provider (endpoint/auth variable names beyond placeholders).
- Approved cloned profile publication configuration and runtime selection wiring.
- Explicit admin publication gating confirmation for approved Chapman voice use.

What is intentionally not implemented in this governance note update:
- No real cloned-voice provider adapter.
- No hardcoded private provider IDs/secrets.
- No committed voice samples/model artifacts/embeddings/private likeness assets.

Current runtime behavior (intended and non-blocking):
- **Standard voice remains active/default** in runtime.
- AskChappy sessions run normally without cloned voice assets/provider/config.
- Missing cloned provider/config/audio is **not** an app runtime blocker.
- Transcript-first TTS constraints remain unchanged.
