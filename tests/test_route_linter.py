
import os, re, pathlib

BANNED = [
    r"/api/v1/voice/chunk",
    r"/api/v1/voice/end",
    r"/api/greet(?![a-zA-Z0-9_/])",
    r"/api/voice/",
    r"legacy_app",
]

REQUIRED = [
    r"/ws/v1/chat",
]

SCAN_DIRS = {'app', 'templates', 'static'}  # bounded scan
ALLOW_EXT = {".py",".js",".ts",".json",".md",".html",".css",".txt",".yml",".yaml",".ini",".cfg"}

def scan_repo(root, max_files=5000):
    matches = { "banned": [], "required": [] }
    required_found = { pat: False for pat in REQUIRED }
    files_scanned = 0
    for p, _, files in os.walk(root):
        rel = os.path.relpath(p, root).split(os.sep)[0]
        if rel not in SCAN_DIRS:
            continue
        for f in files:
            if files_scanned >= max_files:
                break
            ext = os.path.splitext(f)[1].lower()
            if ext and ext not in ALLOW_EXT:
                continue
            fp = os.path.join(p, f)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except Exception:
                continue
            files_scanned += 1
            for pat in BANNED:
                if re.search(pat, txt):
                    matches["banned"].append((fp, pat))
            for pat in REQUIRED:
                if re.search(pat, txt):
                    required_found[pat] = True
    matches["required"] = [pat for pat, ok in required_found.items() if ok]
    return matches, files_scanned

def test_route_linter_guardrails():
    root = pathlib.Path(__file__).resolve().parents[1]
    matches, files_scanned = scan_repo(str(root))
    assert files_scanned > 50, f"Scanned too few files ({files_scanned}); check SCAN_DIRS/ALLOW_EXT"
    assert not matches["banned"], f"Banned routes/symbols present: {matches['banned']}"
    assert "/ws/v1/chat" in matches["required"], "Required WS route reference '/ws/v1/chat' not found"
