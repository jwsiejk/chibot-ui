
import os, sys, pathlib, json, time, importlib, io, re

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ["USE_MOCK_VENDORS"] = "1"   # ensure tests never call network

def import_app():
    app_mod = importlib.import_module("app.asgi_gateway")
    flask_app = getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)
    assert flask_app is not None, "Flask app not exposed"
    return flask_app

def read(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""

def glob(pattern):
    return [str(p) for p in REPO.rglob(pattern)]

def test_admin_log_sse_and_emitters():
    app = import_app()
    c = app.test_client()
    # Open SSE endpoint as a streaming response
    rv = c.get("/api/v1/admin/logs", buffered=True)
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="ignore")
    # We expect at least a 'hello' banner line and then more when we do actions
    assert "kind" in body and "admin_log" in body

    # Trigger a few events
    c.post("/api/v1/admin/config/update", json={"updates":{"theme":"dark"}})
    c.post("/api/v1/admin/layouts/publish", json={"breakpoint":"desktop","state":{"x":1}})
    # re-open to read new buffer easily
    rv2 = c.get("/api/v1/admin/logs", buffered=True)
    body2 = rv2.data.decode("utf-8", errors="ignore")
    assert '"kind":"config_update"' in body2
    assert '"kind":"layout_publish"' in body2

def test_vendor_lanes_guarded():
    # STT provider should exist and respect language lock + normalization (mocked path)
    stt = importlib.import_module("app.providers.stt")
    p = stt.get_stt_provider()
    txt = p.transcribe(b"abc", "audio/webm", language="en")
    assert isinstance(txt, str)
    # normalization hook (mock path uses simple transform that includes 'transcript')
    assert "transcript" in txt.lower()

    # TTS provider exists and returns audio + visemes (mock path)
    tts = importlib.import_module("app.providers.tts")
    t = tts.get_tts_provider()
    audio, vis = t.synthesize_with_visemes("Hello there")
    assert isinstance(audio, (bytes, bytearray)) and len(audio) > 0
    assert isinstance(vis, list) and len(vis) > 0 and "t_ms" in vis[0]

def test_route_linter_no_legacy():
    # Fail if any legacy routes exist (e.g., '/api/v1/greet' or '/api/v0')
    files = glob("app/**/*.py")
    content = "\\n".join(read(f) for f in files)
    bad = [
        "/api/v1/greet", "/api/v0", "/orchestration", "/api/greeting",
        "legacy_app", "legacy_routes", "/api/voice/stt"  # ensure only v1 variant used
    ]
    offenders = [b for b in bad if b in content]
    assert not offenders, f"Found legacy surface(s): {offenders}"

def test_profile_gate_ui_marker():
    # Check that index.html has a Start button disabled and a small gating script
    pages = glob("templates/*.html") + glob("templates/*.html")
    assert pages, "No templates found"
    html = "\\n".join(read(p) for p in pages if "index" in p or "base" in p)
    assert 'id="startBtn"' in html or "start-button" in html
    # Should include a fetch to /api/v1/profile/get and code to enable
    assert "/api/v1/profile/get" in html
    assert "disabled" in html.lower()
