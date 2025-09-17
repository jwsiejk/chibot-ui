import re
from pathlib import Path
LEGACY=[r"/api/greet\b", r"/api/chat\b", r"/api/voice\b", r"/ws/chat\b", r"legacy_app\b"]
def test_no_legacy_routes():
    root=Path(__file__).resolve().parents[1]; offenders=[]
    for base in ['app','static','templates','scripts','config']:
        b=root/base
        if not b.exists(): continue
        for p in b.rglob("*"):
            if not p.is_file(): continue
            txt=p.read_text(encoding='utf-8',errors='ignore')
            for pat in LEGACY:
                if re.search(pat, txt): offenders.append((str(p), pat))
    assert not offenders, f"Found banned legacy patterns: {offenders}"
def test_no_sse_on_ws_path():
    from pathlib import Path
    bad=[]
    for p in (Path(__file__).resolve().parents[1]/'app'/'ws').rglob("*.py"):
        txt=p.read_text(encoding='utf-8',errors='ignore')
        if '/ws/v1/chat' in txt and 'text/event-stream' in txt: bad.append(str(p))
    assert not bad
