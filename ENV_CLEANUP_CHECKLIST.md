# ENV cleanup checklist

## Client surface
- Active entrypoints: `app/static/js/app.js` (loaded via `{{ MAIN_BUNDLE_SRC }}`), `app/static/js/main_entry.js` (esbuild entry).
- Supporting scripts from templates: `/static/js/auth_ui.js`, `/static/js/state.js`, `/static/js/ui/statusbar.js`, `/static/js/admin_logs.js`, `/admin/ui/config_panel.js`.
- Bundles: build produces `app/static/dist/main.[hash].js` with `app/static/dist/manifest.json`; `.gitignore` keeps the dist directory untracked and there are no legacy bundles checked in.
- Audio/WS modules in use: `audio/capture_runtime.js`, `audio/pcm_sender.js`, `audio/ws_audio_runtime.js`, `audio/vad_client.js`, `ws_client.js`, `ws/telemetry.js`.
- Archived/removed: none; legacy variants were not found.

## AppState invariants
- `window.AppState` initialized once near the top of `app/static/js/app.js` with policy + phase defaults.
- Startup log: `AskChip AppState debug` emitted from `app/static/js/app.js`.
- Safe accessor helpers:
  - `app/static/js/app.js`: `const AppState = typeof window !== "undefined" ? window.AppState : undefined;` plus `getAppState()` helper.
  - `app/static/js/ws/telemetry.js`, `app/static/js/ws_client.js`, and `app/static/js/audio/ws_audio_runtime.js` fetch AppState through local `getAppState()` helpers before use.

## Audio path invariants
- Canonical chain: `capture_runtime → pcm_sender → ws_audio_runtime → ws_client → WebSocket`.
- Single sources for factories: `createCaptureRuntime` (capture_runtime.js), `initPcmSender` (pcm_sender.js), `createWsAudioRuntime` (ws_audio_runtime.js); no legacy imports remain.
- `ws_client.js` loads audio modules via `versionModule.importV` with no fallbacks to alternate files.

## Logging expectations
- On page load, console should show:
  - `AskChip build: ...` (from `app.js`)
  - `AskChip AppState debug: ...` (from `app.js`)
  - `AskChip ws_audio_runtime loaded ...` (from `ws_audio_runtime.js`)
  - `AskChip pcm_sender initialized ...` (from `pcm_sender.js`)
- On mic start/update: at least one `AskChip pcm_sender.gates { ... }` log from `updatePcmSenderState`.

## Human validation steps
- Build: `npm run build:js` (outputs to `app/static/dist/` and writes `manifest.json`).
- Deploy check: ensure `app/static/dist/manifest.json` is present so `{{ MAIN_BUNDLE_SRC }}` points at `main.[hash].js`; otherwise the app falls back to `/static/js/app.js`.
- Browser console validation:
  1. Load the page; confirm the startup logs above appear once.
  2. Start the microphone; confirm `AskChip pcm_sender.gates` logs emit and that chunks progress past gating.
