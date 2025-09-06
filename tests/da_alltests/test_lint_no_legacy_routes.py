import os, re

BANNED = [r"/api/v1/greet", r"legacy_app", r"sendChat\("]

def test_no_legacy_routes():
    repo = os.getcwd()
    offenders = []
    for root, _, files in os.walk(repo):
        for f in files:
            if f.endswith((".py", ".js", ".html")):
                path = os.path.join(root, f)
                try:
                    txt = open(path, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                for pat in BANNED:
                    if re.search(pat, txt):
                        offenders.append(f"{path}: {pat}")
    assert not offenders, "Banned legacy patterns found:\\n" + "\\n".join(offenders)
