
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

def test_admin_log_feed_and_emitters(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    app = import_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user"] = {"email": "admin@example.com"}

    rv = c.get("/api/v1/admin/logs")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["ok"] is True
    baseline = len(body["events"])

    # Trigger a few events
    c.post("/api/v1/admin/config/update", json={"updates":{"theme":"dark"}})
    c.post("/api/v1/admin/layouts/publish", json={"breakpoint":"desktop","state":{"x":1}})
    rv2 = c.get("/api/v1/admin/logs")
    events = rv2.get_json()["events"]
    kinds = [evt.get("kind") for evt in events[baseline:]]
    assert "config_update" in kinds
    assert "layout_publish" in kinds

def test_vendor_lanes_guarded():
    # STT provider should exist and respect language lock + normalization (mocked path)
    stt = importlib.import_module("app.services.stt_provider")
    p = stt.get_stt_provider({})
    txt = p.transcribe(b"abc", language="en")
    assert isinstance(txt, str)
    # normalization hook (mock path uses simple transform that includes 'transcript')
    assert "transcript" in txt.lower()

    # TTS provider exists and returns audio + visemes (mock path)
    tts = importlib.import_module("app.services.tts_provider")
    t = tts.get_tts_provider({})
    audio, vis = t.synth("Hello there")
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
