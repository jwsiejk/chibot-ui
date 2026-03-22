# AskChip Local v1 Acceptance

## Status legend
- **Met**
- **Partially met**
- **Not met**

## Acceptance summary
| Area | Status | Notes | Acceptable for v1? |
| --- | --- | --- | --- |
| Local-first runtime | Met | Frontend/backend stay localhost-first and config/readiness now expose local runtime details. | Yes |
| Typed chat contract | Met | Typed chat still writes canonical `text` transcript rows and streams assistant deltas through the same contract. | Yes |
| Unified canonical transcript | Met | Canonical transcript still uses `text`, with no reintroduced `content` field. | Yes |
| Phase 5 push-to-talk voice input | Met | PTT lifecycle and STT commit flow remain unchanged, with diagnostics surfacing mic/device failures and stop markers. | Yes |
| Phase 6 assistant speech | Met | Completed-message Kokoro playback remains separate from typed chat/PTT and interruption markers stay diagnostic-only. | Yes |
| Diagnostics / timings | Met | Diagnostics now show readiness/warm-up, runtime config, stop markers, and per-turn timing visibility. | Yes |
| Error handling / recovery visibility | Partially met | UI now exposes websocket, mic, WebRTC, STT/TTS, and Ollama readiness failures, but DB failures still surface primarily through existing API error banners rather than a dedicated diagnostics badge. | Yes |
| Modular architecture / file-size guardrails | Partially met | Changes stayed additive and modular, but some pre-existing large files remain near the project guardrails. | Yes |
| Out-of-scope items correctly excluded | Met | No wake word, always-open mic, tools, RAG, auth, cloud sync, Docker, or WebRTC transport expansion were added. | Yes |

## Remaining gaps
1. **Dedicated DB health probing is still absent.** The shell reports real API/database failures when they occur, but there is no separate proactive DB readiness probe yet. This is acceptable for v1 because failures still surface honestly without adding a second diagnostics subsystem.
2. **Warm-up is intentionally modest.** Ollama warm-up is enabled by default, while TTS warm-up stays optional/off by default to avoid destabilizing startup on constrained local hardware. This is acceptable for v1 because readiness clearly reports whether warm-up ran or failed.
3. **WebRTC remains diagnostics-only.** The drawer shows foundation state, but typed chat, voice upload, and TTS still do not depend on WebRTC transport. This is acceptable for v1 and matches the contract.
