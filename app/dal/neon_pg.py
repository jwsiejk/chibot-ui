import os, time, json, sqlite3
from typing import Dict, Any, List, Optional

_DB = None
_DIALECT = None

def _connect():
    """Connect to SQLite (file) or Postgres depending on DATABASE_URL."""
    global _DB, _DIALECT
    if _DB:
        return _DB
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn or dsn.startswith("sqlite:///"):
        _DIALECT = "sqlite"
        path = dsn[len("sqlite:///"):] if dsn.startswith("sqlite:///") else "/tmp/askchip.sqlite3"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        return _DB
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    if dsn.startswith("postgresql://"):
        import psycopg
        _DIALECT = "postgresql"
        _DB = psycopg.connect(dsn)
        return _DB
    # default to sqlite if unknown
    _DIALECT = "sqlite"
    _DB = sqlite3.connect("/tmp/askchip.sqlite3", check_same_thread=False)
    _DB.row_factory = sqlite3.Row
    return _DB

def _exec(sql: str, params: Optional[list] = None, fetch: bool = False):
    con = _connect()
    cur = con.cursor()
    cur.execute(sql, params or [])
    if fetch:
        rows = cur.fetchall()
        cur.close()
        return rows
    con.commit()
    cur.close()

def ensure_schema():
    """Create tables in the current dialect."""
    _connect()
    if _DIALECT == "postgresql":
        ddl = [
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at DOUBLE PRECISION)",
        ]
    else:
        ddl = [
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at REAL)",
        ]
    for stmt in ddl:
        _exec(stmt)

def load_profile(email: str) -> Dict[str, Any]:
    ensure_schema()
    rows = _exec("SELECT profile_json FROM profiles WHERE email=%s" if _DIALECT=="postgresql"
                 else "SELECT profile_json FROM profiles WHERE email=?", [email], fetch=True)
    if not rows: return {}
    profile_json = rows[0][0] if _DIALECT=="postgresql" else rows[0]["profile_json"]
    return json.loads(profile_json or "{}")

def save_profile(email: str, prof: Dict[str, Any]):
    ensure_schema()
    now = time.time()
    if _DIALECT=="postgresql":
        _exec("""INSERT INTO profiles (email, profile_json, updated_at) VALUES (%s,%s,%s)
                 ON CONFLICT (email) DO UPDATE SET profile_json=EXCLUDED.profile_json, updated_at=EXCLUDED.updated_at""",
              [email, json.dumps(prof), now])
    else:
        _exec("""INSERT INTO profiles (email, profile_json, updated_at) VALUES (?,?,?)
                 ON CONFLICT(email) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
              [email, json.dumps(prof), now])
