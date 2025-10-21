# Repo Structure (AskChip v2)

> Single-path **v2** only. WS endpoint: **`/ws/v2/chat`** (subprotocol `chat.v2`).  
> Templates root is **`app/templates/`** (no top-level `templates/`).

├─ app/
│ ├─ asgi_gateway.py # routes /ws/v2/chat; health; (v1 removed/410)
│ ├─ ws/
│ │ └─ adapter.py # WS I/O only (thin shell)
│ ├─ voice_v2/ # v2 core (package-level versioning)
│ │ ├─ engine.py # session orchestrator (small)
│ │ ├─ policy_manager.py # single authority: mode/allow_auto_vad/barge_in_enabled/acwr
│ │ ├─ tts_tracker.py # real tts.start/tts.end + post_hold timing
│ │ ├─ gate_controller.py # mic gate + automatic barge-in behavior
│ │ ├─ asr_manager.py # Deepgram primary; Speechmatics secondary adapter
│ │ ├─ nlu.py # analyze(final_text, ctx) -> NLU object (one per turn)
│ │ ├─ nlg.py # generate(nlu, dialog_ctx, persona) -> NLG object
│ │ └─ dialog_policy.py # decide(nlu, ctx, persona) -> directive
│ ├─ telemetry/
│ │ ├─ bus.py # in-proc pub/sub (EVT_* schema)
│ │ └─ exporter.py # hand-off bundle writer (policy diffs, WS taps, flow timeline)
│ ├─ policy/
│ │ └─ loader.py # policy defaults (incl. telemetry block); no runtime ACWR input
│ └─ templates/ # the ONLY Jinja root
│
├─ static/
│ └─ v2/ # new client (waveform UI; chat.v2)
│ ├─ runtime/ # ws.js, send.js, telemetry.js
│ ├─ policy/ # InteractionPolicy.js (ACWR stickiness)
│ ├─ audio/ # player.js (onplay/onended), recorder.js
│ └─ ui/ # waveform.js, stateBadges.js
│
├─ docs/
│ ├─ 00_CONTEXT.md # what AskChip is (canonical)
│ ├─ 10_CONTRACT_WS.md # chat.v2 protocol + telemetry policy block
│ ├─ 15_NLU_NLG.md # NLU/NLG contracts & integration points
│ ├─ 20_ARCH_BUILD_ORDER.md # builds 1–8 + NLU/NLG wiring & telemetry
│ ├─ 30_ADR.md # architecture decision records (append-only)
│ └─ 05_REPO_STRUCTURE.md # (this file)
│
├─ prompts/
│ ├─ chatgpt/
│ │ └─ SESSION_BOOTSTRAP.md # paste at top of every ChatGPT session (SSOT)
│ └─ codex/
│ ├─ BUILD_01.md … BUILD_08.md # per-build Codex prompts, micro-tasked
│
├─ ops/
│ ├─ VERSION.yml # engine + prompt_pack versions
│ ├─ ENV_VARS.yml # authoritative env registry (names, reqd/optional)
│ ├─ CHECKLIST_PR.md # PR “definition of done”
│ └─ ci_checks.md # (optional) guidance for CI guardrails
│
├─ env/
│ ├─ .env.example # names only; no secrets
│ └─ .env.development.local # git-ignored
│
├─ exports/ # runtime bundles (git-ignored)
└─ .gitignore


## Notes & invariants

- **Endpoint & contract:** only `/ws/v2/chat` with subprotocol `chat.v2`. All `policy.interaction` frames **must** include:  
  `mode`, `allow_auto_vad`, `barge_in_enabled`, `auto_commit_when_ready`, and the **telemetry** block.
- **ACWR precedence:** `effective = policy_state AND admin_switch` (no runtime cfg).
- **Telemetry:** runtime-toggable via policy (`enabled`, `level`, `categories`, `redaction`, `sampling`); applied on both client & server.
- **ASR:** Deepgram primary; Speechmatics secondary (switch via admin/env). Whisper not used.
- **UI:** waveform + state badges; no avatar/visemes in v2.

