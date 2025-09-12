
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]

def read(p): 
    return pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")

def test_index_has_required_assets():
    s = read(ROOT / "templates" / "index.html")
    assert "/static/css/app.css" in s
    for js in ["csrf.js","voice.js","ws.js","auth_gate.js","app.js"]:
        assert f"/static/js/{js}" in s

def test_css_defines_hidden_and_modal():
    css = read(ROOT / "static" / "css" / "app.css")
    assert ".hidden" in css or "[hidden]" in css
    assert ".modal" in css and ".sheet" in css

def test_ws_url_v1_in_ws_js():
    js = read(ROOT / "static" / "js" / "ws.js")
    assert "/ws/v1/chat" in js
