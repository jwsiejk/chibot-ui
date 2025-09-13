
from app import create_app

def _rules(app):
    rules = set()
    for r in app.url_map.iter_rules():
        rules.add(r.rule.rstrip("/"))
    return rules

def test_public_surfaces_present_and_legacy_absent():
    app = create_app()
    client = app.test_client()
    rules = _rules(app)
    # Public surfaces (HTTP)
    assert "/api/v1/health" in rules, "Missing /api/v1/health"
    assert "/api/v1/greet" in rules, "Missing /api/v1/greet"
    assert "/api/v1/chat" in rules or "/api/v1/chat" in {r + "/" for r in rules}, "Missing /api/v1/chat"
    # Voice chunk must exist
    assert "/api/v1/voice/chunk" in rules, "Missing /api/v1/voice/chunk"
    # Legacy voice endpoints must be absent OR return 404/410
    for legacy in ["/api/v1/voice/stt", "/api/v1/voice/tts-with-visemes"]:
        if legacy in rules:
            resp = client.post(legacy, json={})
            assert resp.status_code in (404, 410, 403), f"{legacy} should be 404/410, got {resp.status_code}"
