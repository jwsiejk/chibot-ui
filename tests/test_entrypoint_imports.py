
import os, re

def test_asgi_entrypoint_symbol_present():
    # Parse file content to avoid running the app in this offline environment
    path = os.path.join("app", "asgi_gateway.py")
    assert os.path.exists(path), "Missing app/asgi_gateway.py"
    txt = open(path, "r", encoding="utf-8").read()
    assert re.search(r'\basgi\b', txt), "Expected 'asgi' symbol in app/asgi_gateway.py"
