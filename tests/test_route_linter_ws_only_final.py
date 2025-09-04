import re
from pathlib import Path

LEGACY = [r"/api/greet\b", r"/api/chat\b", r"/api/voice\b", r"/ws/chat\b", r"legacy_app\b"]
SOURCE_DIRS = ["app", "static", "templates", "scripts", "config"]
SUFFIXES = {".py", ".js", ".html", ".css", ".json"}

def test_no_legacy_routes():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for name in SOURCE_DIRS:
        base = root / name
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SUFFIXES:
                continue
            txt = p.read_text(encoding='utf-8', errors='ignore')
            for pat in LEGACY:
                if re.search(pat, txt):
                    offenders.append((str(p), pat))
    assert not offenders, f"Found banned legacy patterns: {offenders}"

def test_no_sse_for_ws_path():
    root = Path(__file__).resolve().parents[1]
    bad = []
    for p in (root / "app" / "ws").rglob("*.py"):
        txt = p.read_text(encoding='utf-8', errors='ignore')
        if '/ws/v1/chat' in txt and 'text/event-stream' in txt:
            bad.append(str(p))
    assert not bad, f"WS files must not serve SSE on /ws/v1/chat: {bad}"

