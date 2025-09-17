
def test_phase2_ws_wires_deepgram():
    txt = open("app/ws/ws_asgi.py","r",encoding="utf-8").read()
    assert "DeepgramClient" in txt, "DeepgramClient must be referenced"
    for token in ("websocket.accept","dg.connect","dg.send","dg.close","KeepAlive","CloseStream","UtteranceEnd","Results"):
        assert token.split(".")[-1] in txt, f"Expected token missing: {token}"

def test_phase2_remove_http_hybrid():
    # Ensure chunk/end routes are not present in v1 voice modules
    import re
    for p in ("app/api_v1/voice.py", "app/api_v1/voice_stream.py"):
        s = open(p,"r",encoding="utf-8").read()
        assert not re.search(r'@bp\.(?:post|route)\(\"/chunk\"', s), f"/chunk route still present in {p}"
        assert not re.search(r'@bp\.(?:post|route)\(\"/end\"', s), f"/end route still present in {p}"
