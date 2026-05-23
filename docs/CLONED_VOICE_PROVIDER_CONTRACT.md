# Cloned Voice Provider Contract (Phase 13)

## Purpose
This Phase 13 contract defines the local-first configuration and readiness gate for optional cloned Chappy voice.

## Defaults
- Standard voice is the active/default voice path.
- Standard voice remains available even when cloned voice config is missing or invalid.
- Optional cloned Chappy voice is never claimed active until readiness checks pass.

## Local-first config contract
`services/askchappy-api/src/voice/clonedVoiceConfig.ts` defines metadata-only config:
- `provider_kind: cloned_chappy`
- `provider_label: Chappy cloned voice`
- `profile_id` (placeholder id, not secret)
- `endpoint` (placeholder/local endpoint, no real private endpoint hardcoded)
- `auth_configured` (boolean status only)
- `consent_confirmed` (must be true)
- `publication_state` (from shared `VoiceProfileState`)
- `enabled` (must be true)

No secrets are stored in this contract.

## Readiness rules
`evaluateClonedVoiceReadiness(config)` gates optional cloned voice:
- missing config => not ready
- invalid/missing required fields => not ready
- `consent_confirmed: false` => not ready
- non-`published` lifecycle state => not ready
- `enabled: false` => not ready

`getVoiceProviderSelection(...)` returns deterministic selection status:
- `selected_provider`
- `active_provider_label`
- `cloned_voice_ready`
- `reasons`
- `standard_voice_active`
- readiness status label (including `Ready for provider adapter`)

## Provider adapter boundary
Phase 13 does **not** implement a real cloned voice provider.
When readiness passes, status is `Ready for provider adapter`; synthesis still stays on the standard voice path until a future approved adapter is added through the Phase 9 TTS provider interface.

## Consent and asset safety
- Consent confirmation is required before cloned voice can be ready.
- Do not commit voice samples, wav/audio files, model artifacts, embeddings, or private likeness assets.
- Do not commit provider secrets or private provider ids.

## Relation to Phase 10 blocker
This contract extends `docs/PHASE10_CLONED_VOICE_BLOCKER_NOTE.md` by defining the approved readiness gate/package needed before any real provider integration.
