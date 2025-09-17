
def test_ws_handler_mentions_required_tokens():
    # Static string scan to avoid running the server
    p = "app/ws/ws_asgi.py"
    txt = open(p,"r",encoding="utf-8").read()
    for token in ("KeepAlive","CloseStream","UtteranceEnd","Results","websocket.accept"):
        assert token in txt, f"Expected token missing in ws_asgi: {token}"
