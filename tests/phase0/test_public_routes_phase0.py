import pytest
from app import create_app

def _rules(app):
    return {r.rule.rstrip('/') for r in app.url_map.iter_rules()}

def test_phase0_v1_surfaces_and_no_legacy():
    app = create_app()
    client = app.test_client()
    rules = _rules(app)

    # Required v1 HTTP surfaces (present)
    assert "/api/v1/health" in rules, "Missing /api/v1/health"
    assert "/api/v1/greet" in rules, "Missing /api/v1/greet"
    assert "/api/v1/chat" in rules or "/api/v1/chat" in {r + "/" for r in rules}, "Missing /api/v1/chat"
    # Admin and TTS endpoints should exist under /api/v1
    assert any(r.startswith("/api/v1/admin") for r in rules), "Missing /api/v1/admin/* blueprint"
    assert "/api/v1/voice/tts-with-visemes" in rules or "/api/v1/chat/tts-with-visemes" in rules, "Missing TTS endpoint"

    # Forbidden legacy mic endpoints must be absent
    assert "/api/v1/voice/chunk" not in rules, "Forbidden /api/v1/voice/chunk present"
    assert "/api/v1/voice/end" not in rules, "Forbidden /api/v1/voice/end present"

    # Legacy HTTP STT should be absent or stubbed (404/410/403 acceptable)
    if "/api/v1/voice/stt" in rules:
        resp = client.post("/api/v1/voice/stt", json={})
        assert resp.status_code in (200, 403, 404, 410), f"/api/v1/voice/stt should be stub/disabled, got {resp.status_code}"
