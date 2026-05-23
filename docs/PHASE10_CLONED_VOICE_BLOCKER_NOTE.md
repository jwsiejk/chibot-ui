# Phase 10 Blocker Note — Chappy cloned voice integration

Phase 10 cloned-voice runtime integration is blocked in the current repository context.

Missing prerequisites:
- Approved provider choice for the Chappy cloned-voice adapter (provider name/SDK/API contract not yet approved in-repo).
- Local configuration values for that approved provider (endpoint + auth variable names/shape beyond placeholders).
- Approved published profile identifier/configuration for local production runtime selection.
- Explicit admin consent/status confirmation wiring for publication gating in local production beyond current static shell text.

What is intentionally not implemented in this blocker PR:
- No fake provider adapter.
- No hardcoded private provider IDs/secrets.
- No committed voice samples/model artifacts/embeddings/private likeness assets.

Current safe behavior retained:
- Local fallback voice remains the active runtime behavior.
- Canonical transcript-first TTS constraints remain unchanged.
