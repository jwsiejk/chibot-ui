
import os, sqlite3
os.environ['DATABASE_URL'] = 'sqlite:////tmp/askchip.sqlite3'

from app import create_app
from app.dal import neon_pg

def _csrf(c):
    r = c.get('/api/v1/csrf')
    return {'X-CSRF-Token': r.headers.get('X-CSRF-Token')}

def setup_users_table():
    # Ensure any profiles row for this email is cleared so we test the users fallback deterministically
    try:
        if neon_pg._table_exists('profiles'):
            neon_pg._exec('DELETE FROM profiles WHERE lower(email)=lower(?)', ['JWSIEJK@PURESTORAGE.COM'])
    except Exception:
        pass

    con = neon_pg._connect()
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT)")
    # Seed uppercase email to verify CI lookup
    cur.execute("INSERT OR REPLACE INTO users(email,name,title,region) VALUES (?,?,?,?)",
                ('JWSIEJK@PURESTORAGE.COM', 'James', 'PTM', 'East'))
    con.commit(); cur.close()

def test_login_reads_existing_profile_from_users_ci():
    setup_users_table()
    app = create_app(); c = app.test_client()
    r = c.post('/api/v1/auth/login', json={'email':'jwsiejk@purestorage.com'}, headers=_csrf(c))
    assert r.status_code == 200
    r = c.get('/api/v1/auth/me')
    j = r.get_json()
    assert j['authenticated'] is True
    assert j['email'].lower() == 'jwsiejk@purestorage.com'
    assert j['profile_complete'] is True
    assert (j.get('profile') or {}).get('name') == 'James'
