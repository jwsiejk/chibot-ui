# Architecture Decision Records (append-only)

- **ADR-0001 (2025-10-21)** — Single-path v2 only: `/ws/v2/chat` (subprotocol `chat.v2`). v1 removed.
- **ADR-0002 (2025-10-21)** — ASR vendors: Deepgram primary, Speechmatics secondary (switchable). Whisper not used.
- **ADR-0003 (2025-10-21)** — Barge-in model: automatic only; toggle `barge_in_enabled` via policy.
- **ADR-0004 (2025-10-21)** — Policy frames must always include: `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, and `telemetry`.
- **ADR-0005 (2025-10-21)** — ACWR precedence: `effective = policy_state AND admin_switch`. No runtime cfg input.
- **ADR-0006 (2025-10-21)** — Templates root standardized to `app/templates/` only.
- **ADR-0007 (2025-10-21)** — NLU/NLG contracts added; exactly one NLU & one NLG object per turn; persona-driven templates later.
- **ADR-0008 (2025-10-21)** — Telemetry policy block controls runtime logging (enabled, level, categories, redaction, sampling).
