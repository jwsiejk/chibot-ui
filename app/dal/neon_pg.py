import os, time, json
from typing import Dict, Any, List

_DB = None
_DIALECT = None

def _connect():
    """
    Supports:
      - sqlite:////abs/path.sqlite
      - postgresql://user:pass@host/db?sslmode=require
      - postgres://... (normalized)
    """
    global _DB, _DIALECT
    if _DB:
        return _DB
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    # Normalize postgres prefix
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    if url.startswith("sqlite:"):
        import sqlite3
        path = url.replace("sqlite://","",1)
        if path.startswith("/"):
            path = path[1:]  # sqlite:////mnt/data/x.sqlite -> //mnt/data/x.sqlite
        _DIALECT = "sqlite"
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        return _DB

    if url.startswith("postgresql://"):
        try:
            import psycopg  # psycopg 3
        except Exception as e:
            raise RuntimeError("psycopg is not installed (add psycopg[binary] to requirements)") from e
        _DIALECT = "postgresql"
        _DB = psycopg.connect(conninfo=url, autocommit=True)
        return _DB

    raise RuntimeError("Unsupported DATABASE_URL scheme: " + url)

def _exec(sql, args=None, fetch=False):
    db = _connect()
    if _DIALECT == "sqlite":
        cur = db.cursor()
        cur.execute(sql, args or [])
        if fetch:
            return cur.fetchall()
        db.commit()
        return None
    # psycopg
    with db.cursor() as cur:
        cur.execute(sql, args or [])
        if fetch:
            return cur.fetchall()
        return None

def ensure_schema():
    """Create tables in the current dialect."""
    if _DIALECT is None:
        _connect()

    if _DIALECT == "sqlite":
        ddl = [
            "CREATE TABLE IF NOT EXISTS admin_settings (version INTEGER PRIMARY KEY AUTOINCREMENT, cfg_json TEXT, created_at REAL);",
            "CREATE TABLE IF NOT EXISTS layouts (id INTEGER PRIMARY KEY AUTOINCREMENT, breakpoint TEXT, version INTEGER, state_json TEXT, note TEXT, created_at REAL);",
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT, created_at REAL, last_seen REAL);",
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at REAL);",
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, email TEXT, persona_id TEXT, started_at REAL, ended_at REAL, summary_json TEXT);",
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, text TEXT, meta_json TEXT, created_at REAL);"
        ]
    else:  # postgresql
        ddl = [
            "CREATE TABLE IF NOT EXISTS admin_settings (version SERIAL PRIMARY KEY, cfg_json TEXT, created_at DOUBLE PRECISION);",
            "CREATE TABLE IF NOT EXISTS layouts (id SERIAL PRIMARY KEY, breakpoint TEXT, version INTEGER, state_json TEXT, note TEXT, created_at DOUBLE PRECISION);",
            "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT, created_at DOUBLE PRECISION, last_seen DOUBLE PRECISION);",
            "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at DOUBLE PRECISION);",
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, email TEXT, persona_id TEXT, started_at DOUBLE PRECISION, ended_at DOUBLE PRECISION, summary_json TEXT);",
            "CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, session_id TEXT, role TEXT, text TEXT, meta_json TEXT, created_at DOUBLE PRECISION);"
        ]
    for stmt in ddl:
        _exec(stmt)
    return True

def save_config(cfg: Dict[str, Any]) -> int:
    ensure_schema()
    if _DIALECT == "postgresql":
        _exec("INSERT INTO admin_settings (cfg_json, created_at) VALUES (%s,%s)", [json.dumps(cfg), time.time()])
        rows = _exec("SELECT COALESCE(MAX(version),0) FROM admin_settings", fetch=True)
        return int(rows[0][0] if rows else 0)
    else:
        _exec("INSERT INTO admin_settings (cfg_json, created_at) VALUES (?,?)", [json.dumps(cfg), time.time()])
        rows = _exec("SELECT COALESCE(MAX(version),0) AS v FROM admin_settings", fetch=True)
        return int(rows[0]["v"] if rows else 0)

def latest_config() -> Dict[str, Any]:
    ensure_schema()
    rows = _exec("SELECT cfg_json FROM admin_settings ORDER BY version DESC LIMIT 1", fetch=True)
    if not rows:
        return {}
    if _DIALECT == "postgresql":
        return json.loads(rows[0][0] or "{}")
    return json.loads(rows[0]["cfg_json"] or "{}")

def save_layout(breakpoint: str, state: Dict[str, Any], note: str = "") -> int:
    ensure_schema()
    if _DIALECT == "postgresql":
        rows = _exec("SELECT COALESCE(MAX(version),0) FROM layouts WHERE breakpoint=%s", [breakpoint], fetch=True)
        v = int(rows[0][0]) + 1 if rows else 1
        _exec("INSERT INTO layouts (breakpoint, version, state_json, note, created_at) VALUES (%s,%s,%s,%s,%s)",
              [breakpoint, v, json.dumps(state), note, time.time()])
    else:
        rows = _exec("SELECT COALESCE(MAX(version),0) AS v FROM layouts WHERE breakpoint=?", [breakpoint], fetch=True)
        v = int(rows[0]["v"]) + 1 if rows else 1
        _exec("INSERT INTO layouts (breakpoint, version, state_json, note, created_at) VALUES (?,?,?,?,?)",
              [breakpoint, v, json.dumps(state), note, time.time()])
    return v

def list_layouts(breakpoint: str) -> List[Dict[str, Any]]:
    ensure_schema()
    rows = _exec("SELECT version, state_json, note, created_at FROM layouts WHERE breakpoint=%s ORDER BY version ASC" if _DIALECT=="postgresql"
                 else "SELECT version, state_json, note, created_at FROM layouts WHERE breakpoint=? ORDER BY version ASC",
                 [breakpoint], fetch=True)
    out = []
    for r in rows or []:
        if _DIALECT=="postgresql":
            version, state_json, note, created_at = r
        else:
            version, state_json, note, created_at = r["version"], r["state_json"], r["note"], r["created_at"]
        out.append({"version": int(version), "state": json.loads(state_json or "{}"), "note": note, "created_at": created_at})
    return out

def get_layout(breakpoint: str, version: int) -> Dict[str, Any]:
    ensure_schema()
    rows = _exec("SELECT state_json FROM layouts WHERE breakpoint=%s AND version=%s" if _DIALECT=="postgresql"
                 else "SELECT state_json FROM layouts WHERE breakpoint=? AND version=?",
                 [breakpoint, version], fetch=True)
    if not rows: return {}
    state_json = rows[0][0] if _DIALECT=="postgresql" else rows[0]["state_json"]
    return json.loads(state_json or "{}")

def upsert_user(email: str, name: str = "", title: str = "", region: str = ""):
    ensure_schema()
    now = time.time()
    if _DIALECT=="postgresql":
        _exec("""INSERT INTO users (email, name, title, region, created_at, last_seen)
                 VALUES (%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (email) DO UPDATE SET last_seen=EXCLUDED.last_seen""",
              [email, name, title, region, now, now])
    else:
        _exec("""INSERT INTO users (email, name, title, region, created_at, last_seen)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(email) DO UPDATE SET last_seen=excluded.last_seen""",
              [email, name, title, region, now, now])

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

def load_profile(email: str) -> Dict[str, Any]:
    ensure_schema()
    rows = _exec("SELECT profile_json FROM profiles WHERE email=%s" if _DIALECT=="postgresql"
                 else "SELECT profile_json FROM profiles WHERE email=?", [email], fetch=True)
    if not rows: return {}
    profile_json = rows[0][0] if _DIALECT=="postgresql" else rows[0]["profile_json"]
    return json.loads(profile_json or "{}")

def ensure_session(session_id: str, email: str, persona_id: str = "chip"):
    ensure_schema()
    # upsert session header if missing
    rows = _exec("SELECT 1 FROM sessions WHERE id=%s" if _DIALECT=="postgresql"
                 else "SELECT 1 FROM sessions WHERE id=?", [session_id], fetch=True)
    if not rows:
        if _DIALECT=="postgresql":
            _exec("INSERT INTO sessions (id, email, persona_id, started_at, ended_at, summary_json) VALUES (%s,%s,%s,%s,%s,%s)",
                  [session_id, email, persona_id, time.time(), None, "{}"])
        else:
            _exec("INSERT INTO sessions (id, email, persona_id, started_at, ended_at, summary_json) VALUES (?,?,?,?,?,?)",
                  [session_id, email, persona_id, time.time(), None, "{}"])

def add_message(session_id: str, role: str, text: str, meta: Dict[str, Any] = None):
    ensure_schema()
    if _DIALECT=="postgresql":
        _exec("INSERT INTO messages (session_id, role, text, meta_json, created_at) VALUES (%s,%s,%s,%s,%s)",
              [session_id, role, text, json.dumps(meta or {}), time.time()])
    else:
        _exec("INSERT INTO messages (session_id, role, text, meta_json, created_at) VALUES (?,?,?,?,?)",
              [session_id, role, text, json.dumps(meta or {}), time.time()])

def list_users() -> List[Dict[str, Any]]:
    ensure_schema()
    rows = _exec("SELECT email, name, title, region, created_at, last_seen FROM users ORDER BY last_seen DESC", fetch=True)
    out = []
    for r in rows or []:
        if _DIALECT=="postgresql":
            out.append({"email": r[0], "name": r[1], "title": r[2], "region": r[3], "created_at": r[4], "last_seen": r[5]})
        else:
            out.append(dict(r))
    return out

def list_sessions(email: str = None) -> List[Dict[str, Any]]:
    ensure_schema()
    if email:
        rows = _exec("SELECT id, email, persona_id, started_at, ended_at, summary_json FROM sessions WHERE email=%s ORDER BY started_at DESC" if _DIALECT=="postgresql"
                     else "SELECT id, email, persona_id, started_at, ended_at, summary_json FROM sessions WHERE email=? ORDER BY started_at DESC",
                     [email], fetch=True)
    else:
        rows = _exec("SELECT id, email, persona_id, started_at, ended_at, summary_json FROM sessions ORDER BY started_at DESC", fetch=True)
    out = []
    for r in rows or []:
        if _DIALECT=="postgresql":
            out.append({"id": r[0], "email": r[1], "persona_id": r[2], "started_at": r[3], "ended_at": r[4], "summary": json.loads(r[5] or "{}")})
        else:
            out.append({"id": r["id"], "email": r["email"], "persona_id": r["persona_id"], "started_at": r["started_at"], "ended_at": r["ended_at"], "summary": json.loads(r["summary_json"] or "{}")})
    return out

def list_messages(session_id: str) -> List[Dict[str, Any]]:
    ensure_schema()
    rows = _exec("SELECT role, text, created_at FROM messages WHERE session_id=%s ORDER BY id ASC" if _DIALECT=="postgresql"
                 else "SELECT role, text, created_at FROM messages WHERE session_id=? ORDER BY id ASC", [session_id], fetch=True)
    out = []
    for r in rows or []:
        if _DIALECT=="postgresql":
            out.append({"role": r[0], "text": r[1], "created_at": r[2]})
        else:
            out.append(dict(r))
    return out

def anonymize_session(session_id: str):
    ensure_schema()
    _exec("UPDATE sessions SET email='anonymized@example.com' WHERE id=%s" if _DIALECT=="postgresql"
          else "UPDATE sessions SET email='anonymized@example.com' WHERE id=?", [session_id])

def load_all_into_memory(memory: Dict[str, Any]):
    # Config (latest)
    try:
        cfg = latest_config()
        if cfg:
            memory.setdefault('configs', {}).update(cfg)
    except Exception:
        pass
    # Latest layout per breakpoint
    try:
        rows = _exec("SELECT breakpoint, MAX(version) FROM layouts GROUP BY breakpoint", fetch=True)
        for r in rows or []:
            if _DIALECT=="postgresql":
                bp, mv = r[0], int(r[1] or 0)
            else:
                bp, mv = r["breakpoint"], int(r["MAX(version)"] or 0)
            if mv:
                memory.setdefault('layouts', {})[bp] = {"version": mv, "state": get_layout(bp, mv)}
    except Exception:
        pass
