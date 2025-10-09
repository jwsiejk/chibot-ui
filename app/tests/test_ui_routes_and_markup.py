
import os, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

def read(p):
    return pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")

def test_no_legacy_routes_strings():
    bad=[]
    for p in list(ROOT.glob("**/*.py")) + list(ROOT.glob("static/**/*.js")) + list(ROOT.glob("templates/**/*.html")):
        s = read(p)
        # Disallow explicit legacy patterns
        if "/api/v1/greet" in s:
            bad.append(str(p))
        # Any /api/ or /ws/ that isn't v1*
        if re.search(r"/(api|ws)/(?!v1\\b)", s):
            bad.append(str(p))
    assert not bad, "Found non-v1 route strings in: " + ", ".join(sorted(set(bad)))

def test_index_has_css_and_js():
    idx = ROOT / "templates" / "index.html"
    s = read(idx)
    assert "static/css/app.css" in s
    assert "static/js/app.js" in s
    # quick sanity: composer + start button exist
    assert 'id="btn_start"' in s and 'id="composer_input"' in s

def test_app_js_ws_v1():
    s = read(ROOT / "static" / "js" / "app.js")
    assert "/ws/v1/chat" in s
