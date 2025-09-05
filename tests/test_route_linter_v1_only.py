import re
from pathlib import Path

BANNED = [
    r"/api/v1/greet\b",
    r"/api/chat\b",
    r"/api/voice\b",
    r"/ws/chat\b",
    r"legacy_app\b",
]

SOURCE_DIRS = {"app", "static", "templates", "scripts", "config"}
SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".json"}

def test_no_legacy_routes_in_source_code():
    root = Path(__file__).resolve().parents[1]
    sources = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in SOURCE_SUFFIXES:
            try:
                rel = p.relative_to(root)
            except Exception:
                continue
            if rel.parts[0] not in SOURCE_DIRS:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            sources.append((str(p), text))
    offenders = []
    for path, text in sources:
        for pat in BANNED:
            if re.search(pat, text):
                offenders.append((path, pat))
    assert not offenders, f"Found banned legacy patterns: {offenders}"
