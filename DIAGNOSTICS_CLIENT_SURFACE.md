# Client surface inventory

## Source tree (app/static/js)
- Entry shell: `app/static/js/app.js` (loaded via `{{ MAIN_BUNDLE_SRC }}` / fallback).
- Bundle entry: `app/static/js/main_entry.js` (esbuild entry used by `tools/build_js.mjs`).
- Audio path modules: `audio/capture_runtime.js`, `audio/pcm_sender.js`, `audio/ws_audio_runtime.js`, `audio/vad_client.js`, `audio/pcm-worklet-processor.js`.
- WS + telemetry: `ws_client.js`, `ws/telemetry.js`, `ws/connection.js`, `ws/policy_runtime.js`, `ws/session_manager.js`, `ws/turns.js`, `ws/frame_parser.js`, `ws/banner_client.js`, `ws/transcript_bridge.js`.
- UI/utility modules referenced by templates: `auth_ui.js`, `state.js`, `ui/statusbar.js`, `version.js`, `errors.js`, `audio_player.js`, `voice/phase_controller.js`, `utils/*`.
- No duplicate or legacy variants found for `ws_client`, `ws_audio_runtime`, `pcm_sender`, or `capture_runtime`.

## Built bundles (app/static/dist)
- `app/static/dist/` is absent in the working tree; `.gitignore` already excludes it.
- esbuild config (`tools/build_js.mjs`) writes `main.[hash].js` and `manifest.json` to `app/static/dist/` when `npm run build:js` is executed.
- Because `manifest.json` is missing, runtime currently falls back to `/static/js/app.js` for `{{ MAIN_BUNDLE_SRC }}`.

## Template references
- `app/templates/index.html`: preloads `{{ MAIN_BUNDLE_SRC }}` and `/static/js/audio/ws_audio_runtime.js`; loads `/static/js/ui/statusbar.js`, `/static/js/auth_ui.js`, `/static/js/state.js`, and the main bundle via `{{ MAIN_BUNDLE_SRC }}` (type="module").
- `app/templates/admin_logs.html`: loads `/admin/ui/config_panel.js` and `/static/js/admin_logs.js` (no dist bundle references).

## Potentially orphaned files
- No HTML template or import references were found for additional/duplicate audio or WS variants; all discovered modules are part of the current path above.
- With `main_entry.js` as the only esbuild entry, any module not imported by `app.js` is effectively unused at runtime. No extra `ws_client`/`pcm_sender`/`ws_audio_runtime` variants are present.

## Notes
- Client bundle selection depends on `app/static/dist/manifest.json` via `app/static_manifest.py`; ensure the dist directory is built and deployed or the app will keep loading the raw `/static/js/app.js` file.
- No historical bundles were removed; `app/static/dist/` remains ignored and should contain only the active `main.[hash].js` build plus `manifest.json` when generated.
