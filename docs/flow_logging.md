# Flow logging backbone

The flow logging backbone collects a normalized view of every live session. It
is designed to make both export-time diagnostics and real-time debugging feel
predictable, even as the system grows new surfaces. This document describes the
core building blocks that power the `/api/v1/flow/*` endpoints and the tooling
layer that sits on top of them.

## Event taxonomy

Flow events are stored in a single append-only timeline. Each event records the
`level`, `phase`, `type`, `who`, and `meta` payload that describe the state
transition. The following taxonomy keeps the surface area predictable:

| Level        | Example types                                             | Notes |
| ------------ | --------------------------------------------------------- | ----- |
| `flow`       | `session_open`, `session_config`, `greet_end`, `diag_latency` | Primary turn/summary events that drive diagnostics. |
| `transition` | `asr_partial_first`, `tts_start`, `llm_start`              | Short-lived instrumentation for turn-by-turn tracing. |
| `debug`      | `client_console_error`, `ws_frame_out`, `payload_sig`, **`flow_dropped`** | Verbose breadcrumbs and health signals surfaced in exports. |

The **`flow_dropped`** breadcrumb is emitted automatically whenever FlowStore
has to discard data. This happens when either the in-memory event deque reaches
its cap or when an attached batch payload exceeds the configured limits. The
breadcrumb includes contextual metadata (`reason`, `count`, `bytes`, and related
IDs) so that long-running sessions never truncate silently.

## Canonical session markers

Certain event types double as invariants for downstream tooling:

- `session_config` — captured once per session and exported verbatim as
  `config/config.json` in hand-off bundles.
- `greet_end` — signals that the greeting turn completed successfully and is
  used by dashboards that track activation rate.
- `diag_latency` — emitted by the backend whenever a diagnostic latency sample
  is recorded, keeping the timeline grounded with server-side numbers.
- `asr_partial_first` — only emitted once per turn, enforcing the ASR invariant
  that partial hypotheses are deduplicated.

The FlowStore helper methods (`snapshot`, `list`, `sessions`) always operate on
materialized `EventRecord` objects, so any UI or automation that inspects the
stream can count on these anchors existing when the corresponding subsystem ran.

## Client breadcrumbs

Browser clients emit breadcrumbs via `POST /api/v1/flow/breadcrumb`. FlowStore
rate-limits breadcrumbs per session (`30` per minute) and normalizes the payload
so each breadcrumb lands under the `client_*` namespace. The UI surfaces the
following hints so operators can filter the noise:

- A “Client” toggle controls whether `debug` → `client_*` events are displayed.
- Rate-limit hit breadcrumbs include a `__warning` field so repeated bursts are
  obvious in both the live trace and exported logs.
- When exports are requested with `privacy.pii_scrub = true`, the client log
  artifacts are scrubbed (emails hashed, tokens summarized, external IPv4
  addresses replaced with hashed placeholders) before they are archived.

## UI controls and export knobs

The flow inspector exposes a set of controls that map directly onto the export
options:

- **Levels filter** — controls which `levels` array is sent to `/flow/handoff`
  and `/flow/export.ndjson`.
- **Privacy guard** — toggles `privacy.pii_scrub`, enabling the export-time
  scrubbing described above. The UI reflects the state via the `X-Flow-PII-Scrubbed`
  response header so operators know what was delivered.
- **Include logs / WebSocket frames** — drives the `options.include.logs` and
  `options.include.ws` switches.
- **Payload cap** — surfaces the `limits.max_bytes` control; exceeding the limit
  now returns a `413` with `{"error": "export_too_large"}` so the operator can
  re-export with a larger cap.

Understanding these levers makes it possible to reproduce production issues in a
few clicks while keeping the exported artefacts free of accidental PII.
