import os
import re
from pathlib import Path

BANNED = [
    r"/api/v1/greet\b",
    r"/api/chat\b",
    r"/api/voice\b",
    r"/ws/chat\b",
    r"legacy_app\b",
]

def test_no_legacy_routes():
    root = Path(__file__).resolve().parents[1]
    # Scan only project files
    sources = []
    for p in root.rglob('*'):
        if p.is_file() and p.suffix in {'.py', '.js', '.html', '.css', '.json', '.txt', '.md'}:
            try:
                text = p.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            sources.append((str(p), text))
    offenders = []
    for path, text in sources:
        for pat in BANNED:
            if re.search(pat, text):
                offenders.append((path, pat))
    assert not offenders, f"Found banned legacy patterns: {offenders}"
