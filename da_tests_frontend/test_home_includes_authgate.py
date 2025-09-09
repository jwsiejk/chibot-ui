
from app.factory import create_app

def test_home_includes_authgate():
    app = create_app()
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "/static/js/auth_gate.js" in body, "auth_gate.js must be loaded by index.html"
