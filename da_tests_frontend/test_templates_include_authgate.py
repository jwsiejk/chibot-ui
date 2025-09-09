
import os

def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()

def test_templates_reference_auth_gate():
    root = "/opt/project" if os.path.exists("/opt/project") else "/mnt/data/workspace"
    paths = [
        os.path.join(root, "templates", "index.html"),
        os.path.join(root, "app", "templates", "index.html"),
    ]
    for p in paths:
        if os.path.exists(p):
            html = _read(p)
            assert "/static/js/auth_gate.js" in html, f"auth_gate not referenced in {p}"
