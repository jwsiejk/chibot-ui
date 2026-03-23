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
| Phase 6 assistant speech | Met | Kokoro playback can start earlier from stable sentence-level chunks of the same canonical assistant message while keeping the same speech rate and interrupt behavior. | Yes |
| Diagnostics / timings | Met | Diagnostics now show best-effort readiness snapshots, bounded warm-up refresh polling until checks settle, runtime config, stop markers, and per-turn timing visibility. | Yes |
| Error handling / recovery visibility | Partially met | UI now keeps readiness fetch/load failures and speech playback/TTS failures visible as diagnostics-only notices without blocking typed chat; DB failures still surface primarily through existing API error banners rather than a dedicated diagnostics badge. | Yes |
| Modular architecture / file-size guardrails | Partially met | Changes stayed additive and modular, but some pre-existing large files remain near the project guardrails. | Yes |
| Out-of-scope items correctly excluded | Met | No wake word, always-open mic, tools, RAG, auth, cloud sync, Docker, or WebRTC transport expansion were added. | Yes |

## Remaining gaps
1. **Dedicated DB health probing is still absent.** The shell reports real API/database failures when they occur, but there is no separate proactive DB readiness probe yet. This is acceptable for v1 because failures still surface honestly without adding a second diagnostics subsystem.
2. **Warm-up is intentionally modest.** Ollama warm-up is enabled by default, while TTS warm-up stays optional/off by default to avoid destabilizing startup on constrained local hardware. Frontend readiness loading is best-effort and, when a snapshot indicates pending warm-up, the shell performs bounded polling until `warmup_active` is false and no checks remain `pending`. This is acceptable for v1 because warm-up progress stays visible without blocking typed chat.
3. **WebRTC remains diagnostics-only.** The drawer shows foundation state, but typed chat, voice upload, and TTS still do not depend on WebRTC transport. This is acceptable for v1 and matches the contract.
4. **Assistant speech is earlier, not faster.** Playback may begin while the assistant message is still streaming, but only from stable sentence-level chunks of the same canonical `text` message. If playback runs out of stable text before generation finishes, state can move back to `thinking` until the next chunk is ready. No alternate transcript shape, SSML path, or injected reaction audio was added.
