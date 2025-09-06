import importlib
def test_entrypoint_and_blueprints():
    g = importlib.import_module('app.asgi_gateway')
    assert hasattr(g, 'asgi'), "asgi entrypoint missing"
    app = getattr(g, 'asgi')
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert '/api/v1/greet' in rules
    assert '/api/v1/chat' in rules
    assert '/api/v1/voice/stt' in rules
    assert '/api/v1/voice/tts-with-visemes' in rules
    assert '/api/v1/admin/logs' in rules
    assert '/ws/v1/chat' in rules
