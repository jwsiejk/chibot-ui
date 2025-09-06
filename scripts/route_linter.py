#!/usr/bin/env python3
import os, re, sys

# Banned patterns (v1-only policy)
BANNED = [
    r"/api/greet(?!\w)",               # legacy greet
    r"/api/chat(?![^\w/]|/v1/)",       # /api/chat not under /v1/
    r"\borchestrator\b",              # legacy 'orchestrator' symbol
]

ROOT = os.path.dirname(os.path.dirname(__file__))

ALLOWED_EXTS = {".py", ".js", ".ts", ".tsx", ".html", ".css", ".json"}
EXCLUDE_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "artifacts", "dist", "build"
}
EXCLUDE_FILES = set()

def scan():
    bad_hits = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            relp = os.path.relpath(os.path.join(root, f), ROOT).replace("\\","/")
            if relp in EXCLUDE_FILES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in ALLOWED_EXTS:
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                for pat in BANNED:
                    for m in re.finditer(pat, text):
                        bad_hits.append((relp, pat, m.group(0)))
            except Exception:
                pass
    return bad_hits

def main():
    hits = scan()
    if hits:
        print("ROUTE LINTER: FAIL")
        for p,pat,val in hits:
            print(f"  {p}: matched banned '{pat}' → '{val}'")
        sys.exit(1)
    else:
        print("ROUTE LINTER: PASS")

if __name__ == "__main__":
    main()