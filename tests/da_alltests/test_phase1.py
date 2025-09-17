
import os, re, json, base64, importlib, sys, types, pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

def find_file(patterns, exts):
    hits = []
    for p in REPO.rglob("*"):
        if p.is_file():
            name = str(p).replace("\\", "/")
            if any(re.search(ptn, name) for ptn in patterns) and any(name.endswith(ext) for ext in exts):
                hits.append(name)
    return hits

def read_text(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""

def test_ui_has_audio_chunk_handling():
    js_files = find_file([r"/static/", r"/assets/"], [".js", ".mjs"])
    assert js_files, "No front-end JS files found under /static or /assets"
    content = "\n".join(read_text(f) for f in js_files)
    assert "audio_chunk" in content, "WS handler for 'audio_chunk' not found in front-end code"
    assert re.search(r"ChunkedAudioPlayer|MediaSource", content), "No evidence of chunked audio playback implementation"

def test_ui_has_viseme_support():
    js_files = find_file([r"/static/", r"/assets/"], [".js", ".mjs"])
    content = "\n".join(read_text(f) for f in js_files)
    assert re.search(r"viseme|VisemeAnimator", content, re.IGNORECASE), "No viseme animation code found"
    # Expect a scheduler-like pattern
    assert re.search(r"(schedule|t_ms).*viseme", content, re.IGNORECASE), "No viseme schedule handling detected"

def test_ui_listens_for_live_apply_signals():
    js_files = find_file([r"/static/", r"/assets/"], [".js", ".mjs"])
    content = "\n".join(read_text(f) for f in js_files)
    # look for explicit cases/messages
    assert "config_updated" in content or "layout_updated" in content, "No live apply listeners for config/layout updates"

def test_vendor_wiring_modules_present():
    # Look for providers modules or equivalent
    possible = find_file([r"/providers/", r"/vendors/", r"/integrations/"], [".py"])
    # If not present, fallback to routes/voice
    if not possible:
        possible = find_file([r"voice", r"tts", r"stt"], [".py"])
    assert possible, "No provider/vendor wiring modules detected"

def test_tts_route_exists_and_mocks_used(monkeypatch):
    # Ensure no external calls: set flag to force mock vendors
    monkeypatch.setenv("USE_MOCK_VENDORS", "1")
    sys.path.insert(0, str(REPO))
    # Try to import the Flask app (app.asgi_gateway exposes 'app' or factory)
    app_mod = None
    for candidate in ["app.asgi_gateway", "app.app", "asgi_gateway", "main"]:
        try:
            app_mod = importlib.import_module(candidate)
            break
        except Exception:
            pass
    assert app_mod is not None, "Could not import app module (expected app.asgi_gateway or similar)"
    # Try to access a Flask app object named 'app' or 'flask_app'
    flask_app = getattr(app_mod, "app", None) or getattr(app_mod, "flask_app", None)
    # Some projects expose 'asgi' or 'application'; try to get underlying Flask app if available
    if flask_app is None and hasattr(app_mod, "asgi"):
        # If it's an ASGI gateway, see if it exposes underlying app via attribute
        flask_app = getattr(app_mod, "flask_app", None)
    assert flask_app is not None, "App module didn't expose a Flask app instance named 'app' or 'flask_app'"
    client = flask_app.test_client()

    rv = client.post("/api/v1/voice/tts-with-visemes", json={"text":"Test"})
    assert rv.status_code in (200, 201), f"TTS route failed with {rv.status_code}: {rv.data[:200]}"
    data = rv.get_json()
    assert data and data.get("ok") is True, f"Unexpected response: {data}"
    assert "audio_b64" in data, "TTS response missing 'audio_b64'"
    assert "visemes" in data, "TTS response missing 'visemes'"
    # audio_b64 should be small in mock
    ab = data["audio_b64"]
    assert isinstance(ab, str) and len(ab) > 0, "audio_b64 empty"

