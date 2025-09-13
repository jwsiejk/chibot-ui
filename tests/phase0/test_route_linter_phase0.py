
import os, re
from pathlib import Path

CODE_DIRS = ["app", "static", "templates"]  # limit to code assets, not docs/tests

BANNED_PATTERNS = [
    r"/api/greet\\b",              # legacy non-v1 greet
    r"/ws/chat\\b",                # legacy ws
    r"/api/chat\\b",               # legacy chat
    r"/api/voice/(stt\\b|tts\\b|tts-with-visemes\\b)",  # legacy voice surfaces
]

def test_no_legacy_route_literals():
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for code_dir in CODE_DIRS:
        pdir = root / code_dir
        if not pdir.exists():
            continue
        for p in pdir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".html", ".css"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat in BANNED_PATTERNS:
                if re.search(pat, text):
                    offenders.append((str(p.relative_to(root)), pat))
    assert not offenders, f"Banned legacy route strings found: {offenders}"

def test_ws_v1_chat_route_expected_literal_present():
    # Ensure we actually reference the correct WS route somewhere in code
    root = Path(__file__).resolve().parents[2]
    hits = []
    for code_dir in CODE_DIRS:
        pdir = root / code_dir
        if not pdir.exists():
            continue
        for p in pdir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".html", ".css"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "/ws/v1/chat" in text:
                hits.append(str(p.relative_to(root)))
    assert hits, "Expected to find '/ws/v1/chat' referenced in code assets."
