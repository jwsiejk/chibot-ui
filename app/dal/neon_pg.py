
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

def _column_exists(table: str, column: str) -> bool:
    try:
        if _DIALECT == "postgresql":
            rows = _exec("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s AND column_name=%s
                LIMIT 1
            """, [table, column], fetch=True)
            return bool(rows)
        else:
            con = _connect()
            cur = con.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            cur.close()
            return column in cols
    except Exception:
        return False

def _fetch_one(sql: str, params: list):
    try:
        rows = _exec(sql, params, fetch=True)
        return rows[0] if rows else None
    except Exception:
        return None


def load_profile(email: str) -> Dict[str, Any]:
    ensure_schema()
    if not email:
        return {}

    # A) Try USERS table first (exact, then case-insensitive)
    if _table_exists("users"):
        if _DIALECT == "postgresql":
            role_select = "role" if _column_exists("users","role") else "NULL"
            row = _fetch_one(f"""
                SELECT (json_build_object(
                  'email',  email,
                  'name',   COALESCE(name,''),
                  'title',  COALESCE(title, COALESCE({role_select}, '')),
                  'region', COALESCE(region,''),
                  'profile_complete',
                    (COALESCE(name,'') <> '' AND COALESCE(COALESCE(title, {role_select}), '') <> '')
                ))::text AS j
                FROM users
                WHERE email=%s
                LIMIT 1
            """, [email])
            if not row:
                row = _fetch_one(f"""
                    SELECT (json_build_object(
                      'email',  email,
                      'name',   COALESCE(name,''),
                      'title',  COALESCE(title, COALESCE({role_select}, '')),
                      'region', COALESCE(region,''),
                      'profile_complete',
                        (COALESCE(name,'') <> '' AND COALESCE(COALESCE(title, {role_select}), '') <> '')
                    ))::text AS j
                    FROM users
                    WHERE lower(email)=lower(%s)
                    LIMIT 1
                """, [email])
            if row:
                jtxt = row[0]
                try:
                    return json.loads(jtxt or "{}")
                except Exception:
                    return {}
        else:
            row = _fetch_one("SELECT email, name, title, region FROM users WHERE email=? LIMIT 1", [email])
            if not row:
                row = _fetch_one("SELECT email, name, title, region FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
            if row:
                keys = row.keys() if hasattr(row, "keys") else []
                rec = {
                    "email": row["email"],
                    "name":  row["name"]  if "name"  in keys else "",
                    "title": row["title"] if "title" in keys else "",
                    "region":row["region"]if "region"in keys else "",
                }
                if (not rec["title"]) and _column_exists("users","role"):
                    r2 = _fetch_one("SELECT role FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
                    if r2:
                        try: rec["title"] = r2["role"]
                        except Exception: pass
                rec["profile_complete"] = bool((rec.get("name") or "").strip() and (rec.get("title") or "").strip())
                return rec

    # B) Then try PROFILES table (exact, then CI)
    if _DIALECT == "postgresql":
        row = _fetch_one("SELECT profile_json FROM profiles WHERE email=%s", [email])
    else:
        row = _fetch_one("SELECT profile_json FROM profiles WHERE email=?", [email])
    if row:
        pj = row[0] if _DIALECT == "postgresql" else row["profile_json"]
        try:
            return json.loads(pj or "{}")
        except Exception:
            return {}

    if _DIALECT == "postgresql":
        row = _fetch_one("SELECT profile_json FROM profiles WHERE lower(email)=lower(%s)", [email])
    else:
        row = _fetch_one("SELECT profile_json FROM profiles WHERE lower(email)=lower(?)", [email])
    if row:
        pj = row[0] if _DIALECT == "postgresql" else row["profile_json"]
        try:
            return json.loads(pj or "{}")
        except Exception:
            return {}

    return {}


    # 1) Try profiles table (exact match)
    if _DIALECT == "postgresql":
        row = _fetch_one("SELECT profile_json FROM profiles WHERE email=%s", [email])
    else:
        row = _fetch_one("SELECT profile_json FROM profiles WHERE email=?", [email])
    if row:
        pj = row[0] if _DIALECT == "postgresql" else row["profile_json"]
        try:
            return json.loads(pj or "{}")
        except Exception:
            return {}

    # 1b) Try profiles table (case-insensitive)
    if _DIALECT == "postgresql":
        row = _fetch_one("SELECT profile_json FROM profiles WHERE lower(email)=lower(%s)", [email])
    else:
        row = _fetch_one("SELECT profile_json FROM profiles WHERE lower(email)=lower(?)", [email])
    if row:
        pj = row[0] if _DIALECT == "postgresql" else row["profile_json"]
        try:
            return json.loads(pj or "{}")
        except Exception:
            return {}

    # 2) Fallback: users table if present (build a profile dict)
    if not _table_exists("users"):
        return {}

    if _DIALECT == "postgresql":
        role_select = "role" if _column_exists("users","role") else "NULL"
        row = _fetch_one(f"""
            SELECT (json_build_object(
              'email',  email,
              'name',   COALESCE(name,''),
              'title',  COALESCE(title, COALESCE({role_select}, '')),
              'region', COALESCE(region,''),
              'profile_complete',
                (COALESCE(name,'') <> '' AND COALESCE(COALESCE(title, {role_select}), '') <> '')
            ))::text AS j
            FROM users
            WHERE email=%s
            LIMIT 1
        """, [email])
        if not row:
            row = _fetch_one(f"""
                SELECT (json_build_object(
                  'email',  email,
                  'name',   COALESCE(name,''),
                  'title',  COALESCE(title, COALESCE({role_select}, '')),
                  'region', COALESCE(region,''),
                  'profile_complete',
                    (COALESCE(name,'') <> '' AND COALESCE(COALESCE(title, {role_select}), '') <> '')
                ))::text AS j
                FROM users
                WHERE lower(email)=lower(%s)
                LIMIT 1
            """, [email])
        if row:
            jtxt = row[0]
            try:
                return json.loads(jtxt or "{}")
            except Exception:
                return {}
    else:
        # SQLite path
        row = _fetch_one("SELECT email, name, title, region FROM users WHERE email=? LIMIT 1", [email])
        if not row:
            row = _fetch_one("SELECT email, name, title, region FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
        if row:
            keys = row.keys() if hasattr(row, "keys") else []
            rec = {
                "email": row["email"],
                "name":  row["name"]  if "name"  in keys else "",
                "title": row["title"] if "title" in keys else "",
                "region":row["region"]if "region"in keys else "",
            }
            # If title empty and 'role' column exists, fetch role
            if (not rec["title"]) and _column_exists("users","role"):
                r2 = _fetch_one("SELECT role FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
                if r2:
                    try:
                        rec["title"] = r2["role"]
                    except Exception:
                        pass
            rec["profile_complete"] = bool((rec.get("name") or "").strip() and (rec.get("title") or "").strip())
            return rec

    return {}

def save_profile(email: str, prof: Dict[str, Any]):
    ensure_schema()
    now = time.time()
    if _DIALECT == "postgresql":
        _exec("""INSERT INTO profiles (email, profile_json, updated_at) VALUES (%s,%s,%s)
                 ON CONFLICT (email) DO UPDATE SET profile_json=EXCLUDED.profile_json, updated_at=EXCLUDED.updated_at""",
              [email, json.dumps(prof), now])
    else:
        _exec("""INSERT INTO profiles (email, profile_json, updated_at) VALUES (?,?,?)
                 ON CONFLICT(email) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
              [email, json.dumps(prof), now])
