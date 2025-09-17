import os, sys

ROOT = os.path.dirname(os.path.dirname(__file__))
FORBIDDEN = [
    "/api/v1/voice/chunk",
    "/api/v1/voice/end",
    "/api/greet",
]

SKIP_DIR_PARTS = {
    "docs",
    "tests", "tests_phase3", "tests_ui_fix",
    "tests_da_alltests", "_da_alltests", "tests_phase3_5",
    "phase0", "phase1", "phase2", "phase3", "phase4",
}
SKIP_FILES = {"route_linter.py"}

def should_skip(path: str) -> bool:
    parts = set(path.replace('\\','/').split('/'))
    if parts & SKIP_DIR_PARTS:
        return True
    bn = os.path.basename(path)
    if bn in SKIP_FILES:
        return True
    return False

def scan():
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if should_skip(dirpath):
            continue
        if any(skip in dirpath for skip in (".venv", "venv", "__pycache__", "node_modules", "dist", "build")):
            continue
        for fn in filenames:
            # Only scan code-like files, not docs
            if not fn.endswith((".py",".js",".ts",".html")):
                continue
            path = os.path.join(dirpath, fn)
            if should_skip(path):
                continue
            try:
                txt = open(path, "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for token in FORBIDDEN:
                if token in txt:
                    bad.append((token, path))
    return bad

if __name__ == "__main__":
    bad = scan()
    if bad:
        print("Forbidden routes/symbols detected:")
        for token, path in bad:
            print(f" - {token} in {path}")
        sys.exit(2)
    print("Route-linter: OK")
    sys.exit(0)
