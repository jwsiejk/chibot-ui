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



def _table_exists(name: str) -> bool:
    try:
        if _DIALECT == "postgresql":
            rows = _exec("SELECT to_regclass(%s)", [name], fetch=True)
            return bool(rows and rows[0][0])
        else:
            rows = _exec("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name], fetch=True)
            return bool(rows)
    except Exception:
        return False

def _load_profile_from_profiles(email: str) -> Dict[str, Any]:
    ensure_schema()
    if not email:
        return {}
    try:
        if _DIALECT == "postgresql":
            rows = _exec("SELECT profile_json FROM profiles WHERE email=%s", [email], fetch=True)
        else:
            rows = _exec("SELECT profile_json FROM profiles WHERE email=?", [email], fetch=True)
        if rows:
            profile_json = rows[0][0] if _DIALECT == "postgresql" else rows[0]["profile_json"]
            return json.loads(profile_json or "{}")
    except Exception:
        pass
    try:
        if _DIALECT == "postgresql":
            rows = _exec("SELECT profile_json FROM profiles WHERE lower(email)=lower(%s)", [email], fetch=True)
        else:
            rows = _exec("SELECT profile_json FROM profiles WHERE lower(email)=lower(?)", [email], fetch=True)
        if rows:
            profile_json = rows[0][0] if _DIALECT == "postgresql" else rows[0]["profile_json"]
            return json.loads(profile_json or "{}")
    except Exception:
        pass
    return {}

def _load_profile_from_users(email: str) -> Dict[str, Any]:
    if not email or not _table_exists("users"):
        return {}
    cols = "email, name, title, region, profile_complete, completed"
    try:
        if _DIALECT == "postgresql":
            rows = _exec(f"SELECT {cols} FROM users WHERE email=%s LIMIT 1", [email], fetch=True)
        else:
            rows = _exec(f"SELECT {cols} FROM users WHERE email=? LIMIT 1", [email], fetch=True)
        if not rows:
            if _DIALECT == "postgresql":
                rows = _exec(f"SELECT {cols} FROM users WHERE lower(email)=lower(%s) LIMIT 1", [email], fetch=True)
            else:
                rows = _exec(f"SELECT {cols} FROM users WHERE lower(email)=lower(?) LIMIT 1", [email], fetch=True)
        if not rows:
            return {}
        row = rows[0]
        if _DIALECT == "postgresql":
            rec = {
                "email": row[0],
                "name": row[1] if len(row)>1 else "",
                "title": row[2] if len(row)>2 else "",
                "region": row[3] if len(row)>3 else "",
            }
            pc = (row[4] if len(row)>4 else None) or (row[5] if len(row)>5 else None)
        else:
            rec = {
                "email": row["email"],
                "name": row.get("name",""),
                "title": row.get("title",""),
                "region": row.get("region",""),
            }
            pc = row.get("profile_complete") or row.get("completed")
        rec["profile_complete"] = bool(pc) or bool((rec.get("name") or "").strip() and (rec.get("title") or "").strip())
        return rec
    except Exception:
        return {}

def load_profile(email: str) -> Dict[str, Any]:
    ensure_schema()
    p = _load_profile_from_profiles(email)
    if p:
        return p
    p = _load_profile_from_users(email)
    if p:
        return p
    return {}

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
