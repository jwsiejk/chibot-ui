
def test_phase2_ws_wires_deepgram():
    txt = open("app/ws/ws_asgi.py","r",encoding="utf-8").read()
    assert "DeepgramClient" in txt, "DeepgramClient must be referenced"
    for token in ("websocket.accept","dg.connect","dg.send","dg.close","KeepAlive","CloseStream","UtteranceEnd","Results"):
        assert token.split(".")[-1] in txt, f"Expected token missing: {token}"

def test_phase2_remove_http_hybrid():
    from app import create_app

    app = create_app()
    rules = {rule.rule.rstrip('/') for rule in app.url_map.iter_rules()}
    assert "/api/v1/voice/chunk" not in rules, "Forbidden /api/v1/voice/chunk present"
    assert "/api/v1/voice/end" not in rules, "Forbidden /api/v1/voice/end present"
