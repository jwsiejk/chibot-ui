# Imports & Event Wiring Diagnostics

**Scope:** Full repo scan under `static/js` and HTML/templates after import realignment.

## Findings

- **CommonJS/UMD:** None found. No occurrences of `module.exports`, `exports.*`, `require()`, or AMD/UMD `define()` wrappers.
- **Duplicate wiring for Start/End/Send:** None found outside `static/js/bootstrap.js`. No other files attach listeners to `#startButton`, `#endButton`, or `#sendButton`.
- **DOMContentLoaded:** Multiple modules listen for `DOMContentLoaded` (e.g., diagnostics/design/admin UI), but they do **not** bind Start/End/Send.
- **Auth Gate:** Only imports `csrf.js` (OK). No `ws.js`/`voice.js` imports.
- **Bootstrap:** Sole owner of Start/End/Send + greet (OK).

## Files scanned
- All `*.js` under `static/js/`
- Templates: `app/templates/index.html`, `app/templatesold/index.html`, `templates/profile.html` (updated to load `bootstrap.js` and `auth_gate.js` as modules).

## Notes
If you later add a page-specific module that needs Start/End/Send, wire it through `bootstrap.js` or expose a single `initUI()` that `bootstrap.js` calls, to avoid duplicate handlers.
