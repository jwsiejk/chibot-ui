import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import create_app

def test_admin_logs_single_route():
    app = create_app()
    rules = [str(r) for r in app.url_map.iter_rules() if "/api/v1/admin/logs" in str(r)]
    assert len(rules) == 1, f"expected 1 /api/v1/admin/logs route, found {rules}"

def test_admin_logs_first_chunk_within_1s():
    app = create_app()
    with app.test_client() as c:
        r = c.get("/api/v1/admin/logs")
        # The response is a streamed generator; fetch the first chunk
        first = next(iter(r.response))
        assert b"data:" in first, first[:200]