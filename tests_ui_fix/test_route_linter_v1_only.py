
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]

def read(p): 
    return pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")

def test_v1_only_routes():
    bad=[]
    targets = list((ROOT/"app").rglob("*.py")) + list((ROOT/"static/js").rglob("*.js")) + list((ROOT/"templates").rglob("*.html"))
    for p in targets:
        s = read(p)
        if "/api/greet" in s and "/api/v1" not in s: bad.append(str(p))
        for m in re.finditer(r"/(api|ws)/(?!v1\\b)", s):
            ctx = s[max(0,m.start()-12):m.end()+12]
            if "app/" in ctx or "static/" in ctx: 
                continue
            bad.append(str(p))
    assert not bad, "Found potential non-v1 routes in: " + ", ".join(sorted(set(bad)))
