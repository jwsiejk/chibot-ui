
import os, importlib, tempfile, shutil
from app import create_app

def _tmp_sqlite_path():
    d = tempfile.mkdtemp(prefix="chip_p2_")
    return d, f"sqlite:///{d}/neon_phase2.sqlite3"

def test_profile_exists_in_neon_skips_profile_gate_and_marks_complete():
    tmpdir, dsn = _tmp_sqlite_path()
    os.environ['DATABASE_URL'] = dsn
    try:
        # Seed Neon profile
        from app.dal import neon_pg
        importlib.reload(neon_pg)
        neon_pg.save_profile('jane@example.com', {"email":"jane@example.com","name":"Jane","title":"SE","region":"West","profile_complete":True})

        app = create_app()
        client = app.test_client()
        # CSRF
        r = client.get('/api/v1/csrf'); token = r.headers.get('X-CSRF-Token')
        # Login
        r = client.post('/api/v1/auth/login', json={'email':'jane@example.com'}, headers={'X-CSRF-Token': token})
        assert r.status_code == 200, r.data
        # Auth status should show complete
        r = client.get('/api/v1/auth/me')
        j = r.get_json()
        assert j['authenticated'] is True
        assert j['profile_complete'] is True
        assert (j['profile'] or {}).get('name') == 'Jane'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ.pop('DATABASE_URL', None)

def test_profile_absent_then_save_enters_main_interface():
    tmpdir, dsn = _tmp_sqlite_path()
    os.environ['DATABASE_URL'] = dsn
    try:
        from app.dal import neon_pg
        importlib.reload(neon_pg)

        app = create_app()
        client = app.test_client()
        r = client.get('/api/v1/csrf'); token = r.headers.get('X-CSRF-Token')

        # Login new user without profile
        r = client.post('/api/v1/auth/login', json={'email':'john@example.com'}, headers={'X-CSRF-Token': token})
        assert r.status_code == 200
        # Should not be complete yet
        r = client.get('/api/v1/auth/me'); j = r.get_json()
        assert j['authenticated'] is True
        assert j['profile_complete'] is False

        # Save profile
        r = client.post('/api/v1/profile', json={'name':'John','title':'SE','region':'East'}, headers={'X-CSRF-Token': token})
        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        # Now complete
        r = client.get('/api/v1/auth/me'); j = r.get_json()
        assert j['profile_complete'] is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ.pop('DATABASE_URL', None)
