# app/dal/neon_pg.py
import os, time, json, sqlite3
from typing import Dict, Any, List, Optional

_DB = None
_DIALECT = None  # "postgresql" | "sqlite"

# --------------------------- Connection & Exec ---------------------------

def _connect():
    """Connect to SQLite (file) or Postgres depending on DATABASE_URL."""
    global _DB, _DIALECT
    if _DB:
        return _DB
    dsn = os.environ.get("DATABASE_URL", "").strip()

    # SQLite (default/fallback)
    if not dsn or dsn.startswith("sqlite:///"):
        _DIALECT = "sqlite"
        path = dsn[len("sqlite:///"):] if dsn.startswith("sqlite:///") else "/tmp/askchip.sqlite3"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        return _DB

    # Heroku-style alias → psycopg
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]

    if dsn.startswith("postgresql://"):
        import psycopg  # type: ignore
        _DIALECT = "postgresql"
        _DB = psycopg.connect(dsn)
        return _DB

    # Default to sqlite if unknown DSN
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


# ------------------------------- Schema ----------------------------------

def ensure_schema():
    """Create tables in the current dialect."""
    _connect()
    if _DIALECT == "postgresql":
        ddl = [
            # JSON profile store (kept for compatibility; reads will use USERS only)
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at DOUBLE PRECISION)",
            # Minimal flat fields for fast gates and durability (source of truth)
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT)"
        ]
    else:
        ddl = [
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at REAL)",
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT)"
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
            rows = _exec(
                """
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name=%s
                   AND column_name=%s
                 LIMIT 1
                """,
                [table, column],
                fetch=True,
            )
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


# ---------------------------- DAL: Profiles/Users ----------------------------

def load_profile(email: str) -> Dict[str, Any]:
    """
    Load a normalized profile dict for the given email.
    **Users-only**: Always read from the `users` table as the single source of truth.
    Returns keys: email, name, title, region, profile_complete
    """
    ensure_schema()
    if not email or not _table_exists("users"):
        return {}

    if _DIALECT == "postgresql":
        role_select = "role" if _column_exists("users", "role") else "NULL"
        row = _fetch_one(
            f"""
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
            """,
            [email],
        )
        if row:
            try:
                return json.loads(row[0] or "{}")
            except Exception:
                return {}
    else:
        # SQLite
        row = _fetch_one(
            "SELECT email, name, title, region FROM users WHERE lower(email)=lower(?) LIMIT 1",
            [email]
        )
        if row:
            rec = {
                "email": row["email"],
                "name": (row["name"] or "").strip(),
                "title": (row["title"] or "").strip(),
                "region": (row["region"] or "").strip(),
            }
            # If title empty and 'role' column exists, try to lift role
            if not rec["title"] and _column_exists("users", "role"):
                r2 = _fetch_one("SELECT role FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
                if r2:
                    try:
                        rec["title"] = r2["role"] or ""
                    except Exception:
                        pass
            rec["profile_complete"] = bool(rec["name"] and rec["title"])
            return rec

    # Not found in users
    return {}


def save_profile(email: str, prof: Dict[str, Any]):
    """
    Save profile JSON into profiles (compat), and mirror minimal fields into users
    so profile survives reloads/gates consistently.
    """
    ensure_schema()
    now = time.time()

    # Persist JSON profile (kept for compatibility with any callers that expect it)
    if _DIALECT == "postgresql":
        _exec(
            """INSERT INTO profiles (email, profile_json, updated_at)
               VALUES (%s,%s,%s)
               ON CONFLICT (email)
               DO UPDATE SET profile_json=EXCLUDED.profile_json, updated_at=EXCLUDED.updated_at""",
            [email, json.dumps(prof), now],
        )
    else:
        _exec(
            """INSERT INTO profiles (email, profile_json, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(email) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
            [email, json.dumps(prof), now],
        )

    # Mirror minimal fields into users (best-effort; do not raise)
    try:
        upsert_user_fields(
            email=email,
            name=prof.get("name"),
            title=prof.get("title"),
            region=prof.get("region"),
        )
    except Exception:
        pass


# --------------------------- Additional helpers ---------------------------

def upsert_user_fields(
    email: str,
    name: Optional[str] = None,
    title: Optional[str] = None,
    region: Optional[str] = None,
) -> None:
    """
    Persist minimal profile fields into the 'users' table so profile survives reloads.
    Only the provided (non-None) fields are updated.
    """
    ensure_schema()
    if not email:
        return

    # Build dynamic SETs so we only update provided fields
    sets: List[str] = []
    params: List[str] = []
    if name is not None:
        sets.append("name=%s" if _DIALECT == "postgresql" else "name=?")
        params.append((name or "").strip())
    if title is not None:
        sets.append("title=%s" if _DIALECT == "postgresql" else "title=?")
        params.append((title or "").strip())
    if region is not None:
        sets.append("region=%s" if _DIALECT == "postgresql" else "region=?")
        params.append((region or "").strip())

    if not sets:
        return

    if _DIALECT == "postgresql":
        # Ensure a row exists; then update only provided fields
        _exec("INSERT INTO users (email) VALUES (%s) ON CONFLICT (email) DO NOTHING", [email])
        sql = "UPDATE users SET " + ", ".join(sets) + " WHERE lower(email)=lower(%s)"
        _exec(sql, params + [email])
    else:
        # SQLite
        _exec("INSERT OR IGNORE INTO users (email) VALUES (?)", [email])
        sql = "UPDATE users SET " + ", ".join(sets) + " WHERE lower(email)=lower(?)"
        _exec(sql, params + [email])


def load_user_profile(email: str) -> Dict[str, Any]:
    """
    Lightweight loader from 'users' only (no profiles overlay).
    """
    ensure_schema()
    if not email or not _table_exists("users"):
        return {}
    if _DIALECT == "postgresql":
        row = _fetch_one(
            "SELECT email, COALESCE(name,''), COALESCE(title,''), COALESCE(region,'') "
            "FROM users WHERE lower(email)=lower(%s) LIMIT 1",
            [email],
        )
        if not row:
            return {}
        e, n, t, r = row
        return {"email": e, "name": (n or "").strip(), "title": (t or "").strip(), "region": (r or "").strip()}
    else:
        row = _fetch_one("SELECT email,name,title,region FROM users WHERE lower(email)=lower(?) LIMIT 1", [email])
        if not row:
            return {}
        return {
            "email": row["email"],
            "name": (row["name"] or "").strip(),
            "title": (row["title"] or "").strip(),
            "region": (row["region"] or "").strip(),
        }
