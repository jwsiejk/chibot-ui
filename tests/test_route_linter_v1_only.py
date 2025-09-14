import re
from pathlib import Path

BANNED = [
    r"/api/greet\b",   # legacy HTTP
    r"/api/chat\b",
    r"/api/voice\b",
    r"/ws/chat\b",     # legacy WS
    r"legacy_app\b",
]

SOURCE_DIRS = {"app", "static", "templates", "config"}
SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".json"}

def test_no_legacy_routes_in_source_code():
    root = Path(__file__).resolve().parents[1]
    sources = []
    for name in SOURCE_DIRS:
        base = root / name
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SOURCE_SUFFIXES:
                continue
            # Skip internal tests or samples inside app/
            relp = str(p.relative_to(root))
            if "/tests/" in relp or relp.startswith("app/tests"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            sources.append((str(p), text))
    offenders = []
    for path, text in sources:
        for pat in BANNED:
            if re.search(pat, text):
                offenders.append((path, pat))
    assert not offenders, f"Found banned legacy patterns: {offenders}"
