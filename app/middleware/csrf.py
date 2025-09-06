from flask import request, jsonify
from ..db import db
from ..security_state import get_csrf
EXEMPT={"/api/v1/auth/csrf","/api/v1/auth/login","/api/v1/auth/logout"}
def csrf_before_request():
    if request.method in ("POST","PUT","PATCH","DELETE"):
        if request.path in EXEMPT: return None
        if not db.get_config().get("csrf_enforced", False): return None
        tok=request.headers.get("X-CSRF-Token")
        if not tok or tok!=get_csrf(): return jsonify({"ok":False,"error":"csrf_failed"}),403
    return None


# --- Ask Chip CSRF policy ---
from urllib.parse import urlparse
ALLOW_SAME_ORIGIN_JSON = True

def _same_origin_ok(req):
    try:
        ori = req.headers.get("Origin","")
        host = req.host_url.rstrip("/")
        return bool(ori) and urlparse(ori).netloc == urlparse(host).netloc
    except Exception:
        return False
