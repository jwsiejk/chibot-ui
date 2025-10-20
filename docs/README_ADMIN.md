# Flow Trace and Admin Console Guide

## Overview
The Admin Flow Inspector provides a live, queryable view of every event recorded during a voice or chat session. It is built on the `FlowStore` timeline and surfaces levels, smart hints, batching metadata, and hand-off tooling so on-call responders can triage issues without engineering support. This guide explains how to drive the console effectively and how to export data for deeper analysis.

## Navigating Flow Trace
1. **Select a session** – Search active session IDs or paste a known ID. The view fetches an initial snapshot and begins tailing the stream.
2. **Tail controls** – The tail widget (Go live/Refresh) toggles between continuous polling and a paused state. Pause to inspect a past window without new events shifting the timeline; resume when ready.
3. **Timeline** – Rows are grouped by rail, time, phase, actor, type, blurb, and meta chips. Click a row to open the drawer with raw JSON, related events, and copy-to-clipboard shortcuts.

## Levels
Events are classified into four levels:
- **Flow** – High-level milestones and human-readable steps.
- **Transition** – Precise edges such as VAD changes, barge-in, or ASR evidence.
- **Debug** – Diagnostic metadata: runtime flags, timers, queue depths, provider plumbing.
- **Raw** – Large payload batches (audio frames, visemes, etc.).

Use the level toggles to include/exclude layers. The inspector now enables Flow, Transition, and Debug by default so exports capture diagnostic breadcrumbs (client audio signals, WS traces, etc.). Toggling resets the event cache and re-fetches with the new level mask. Raw batches stream efficiently; avoid enabling them unless you need the payloads.

## Grouping Modes
The grouping selector changes how rows render:
- **Chronological** (default) – Strict event order by relative timestamp.
- **By Phase** – Groups into session/turn/guardrail/etc. columns.
- **By Turn** – Buckets events by `turn_id` and expands children inline.

Group changes recompute the tree and persist in the URL hash for easy sharing.

## Filters, Tail, and Chips
- Text filter matches type, phase, who, or blurb substrings.
- Turn filter focuses on a specific turn number.
- Clicking a meta chip adds a removable filter chip.
- The tail state indicator shows Live/Paused; use “Refresh” to fetch once when paused.

## Smart Hints
The hint bar summarizes heuristics from `FlowStore._compute_hints` (e.g., `asr_recovered`, `tts_slow`, queue pressure). Click a hint badge to jump to anchor events. Hints recalc on every fetch and reflect the currently loaded levels.

## Safety Injectors
`FlowStore` automatically closes long-running confirmations, TTS streams, or LLM spans. Injected events carry `meta.__warning` values (`inferred_close`, `forced_close`) so you can spot them quickly.

## Exporting Data
- **Export NDJSON** – Streams the current session with selected levels. Use this when you need the full, unredacted timeline.
- **Export Redacted** – Masks sensitive text, payload bodies, and device labels for safer sharing.
- **Copy link** – Captures the current filters, levels, and grouping in the URL hash.
- **Hand off to ChatGPT** – Downloads a ZIP containing redacted NDJSON (`flow.ndjson`), the default prompt (`prompt.txt`), and `meta.json` summarizing sensitive payload fingerprints. With the default level mask the NDJSON includes Flow, Transition, and Debug events so the archive mirrors the live timeline.

### Example NDJSON
A lightweight example lives at [`docs/examples/flow_session_sample.ndjson`](examples/flow_session_sample.ndjson). Each line is a single event JSON object:
```
{"id":"e_00001","t_rel_ms":0,"level":"flow","phase":"session","type":"session_open","who":"system","meta":{}}
{"id":"e_00002","t_rel_ms":120,"level":"transition","phase":"turn","type":"asr_partial_first","who":"client","meta":{"turn_id":"1"}}
```

## Forensic Mode
Forensic mode temporarily widens the level mask, expands the full tree, and freezes the poll interval for 10 seconds. Use it when you need a dense capture of a tricky sequence (e.g., ASR+LLM races). Exiting restores your previous level selections and expansion state.

## Sample ChatGPT Prompt
```
Analyze the redacted flow transcript and identify:
1. Primary failure modes or regressions.
2. Evidence (event IDs + brief justification).
3. The smallest viable fix or mitigation.
4. Validation steps to confirm the fix.

Return a concise incident summary plus a bullet checklist for responders.
```

Keep the prompt in the hand-off ZIP if you need ChatGPT to triage automatically.
