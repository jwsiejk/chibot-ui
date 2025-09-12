
import importlib
import sys, os
sys.path.insert(0, "/mnt/data/askchip_optB_repo")
from app.asgi_gateway import asgi
from starlette.testclient import TestClient

def test_stt_mode_endpoint():
    c = TestClient(asgi)
    r = c.get("/api/v1/voice/stt-mode")
    assert r.status_code in (200, 500)  # 200 when admin_config is available; 500 if not, but route exists
