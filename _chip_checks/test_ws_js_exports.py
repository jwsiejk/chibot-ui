from pathlib import Path
p = Path(__file__).resolve().parents[1] / "static/js/ws.js"
code = p.read_text(encoding="utf-8")
assert "export function openWS" in code, "openWS must be exported"
assert "export function waitWSOpen" in code, "waitWSOpen must be exported"