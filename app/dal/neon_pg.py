import os, sqlite3, time, json
from typing import Dict, Any, List

_DB = None
_PATH = None

def _connect():
    global _DB, _PATH
    if _DB: return _DB
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    # sqlite:////abs/path
    if url.startswith("sqlite:"):
        path = url.replace("sqlite://","",1)
        if path.startswith("/"):
            # sqlite:////mnt/data/x.sqlite -> //mnt/data/x.sqlite
            path = path[1:]
        _PATH = path
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        return _DB
    # For tests we only implement sqlite. In prod, bind to Postgres driver.
    raise RuntimeError("Only sqlite DATABASE_URL supported in tests")

DDL = [
    "CREATE TABLE IF NOT EXISTS admin_settings (version INTEGER PRIMARY KEY AUTOINCREMENT, cfg_json TEXT, created_at REAL);",
    "CREATE TABLE IF NOT EXISTS layouts (id INTEGER PRIMARY KEY AUTOINCREMENT, breakpoint TEXT, version INTEGER, state_json TEXT, note TEXT, created_at REAL);",
    "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT, created_at REAL, last_seen REAL);",
    "CREATE TABLE IF NOT EXISTS profiles (email TEXT PRIMARY KEY, profile_json TEXT, updated_at REAL);",
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, email TEXT, persona_id TEXT, started_at REAL, ended_at REAL, summary_json TEXT);",
    "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, text TEXT, meta_json TEXT, created_at REAL);"
]

def ensure_schema():
    db = _connect()
    cur = db.cursor()
    for stmt in DDL: cur.execute(stmt)
    db.commit()
    return True

def save_config(cfg: Dict[str, Any]) -> int:
    db = _connect(); cur = db.cursor()
    cur.execute("INSERT INTO admin_settings (cfg_json, created_at) VALUES (?, ?)", (json.dumps(cfg), time.time()))
    db.commit()
    return int(cur.lastrowid or 0)

def latest_config() -> Dict[str, Any]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT cfg_json FROM admin_settings ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    return json.loads(row["cfg_json"]) if row else {}

def save_layout(breakpoint: str, state: Dict[str, Any], note: str = None) -> int:
    db = _connect(); cur = db.cursor()
    # Next version is prior max+1 per breakpoint
    cur.execute("SELECT COALESCE(MAX(version),0) AS v FROM layouts WHERE breakpoint=?", (breakpoint,))
    v = int(cur.fetchone()["v"]) + 1
    cur.execute("INSERT INTO layouts (breakpoint, version, state_json, note, created_at) VALUES (?,?,?,?,?)",
                (breakpoint, v, json.dumps(state), note or "", time.time()))
    db.commit()
    return v

def list_layouts(breakpoint: str) -> List[Dict[str, Any]]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT version, state_json, note, created_at FROM layouts WHERE breakpoint=? ORDER BY version ASC", (breakpoint,))
    out = []
    for r in cur.fetchall():
        out.append({"version": int(r["version"]), "state": json.loads(r["state_json"] or "{}"), "note": r["note"], "created_at": r["created_at"]})
    return out

def get_layout(breakpoint: str, version: int) -> Dict[str, Any]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT state_json FROM layouts WHERE breakpoint=? AND version=?", (breakpoint, version))
    r = cur.fetchone()
    return json.loads(r["state_json"]) if r else {}

def upsert_user(email: str, name: str = None, title: str = None, region: str = None):
    db = _connect(); cur = db.cursor()
    now = time.time()
    # Upsert
    cur.execute("INSERT INTO users (email, name, title, region, created_at, last_seen) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET last_seen=excluded.last_seen", (email, name or "", title or "", region or "", now, now))
    db.commit()

def save_profile(email: str, prof: Dict[str, Any]):
    db = _connect(); cur = db.cursor()
    cur.execute("INSERT INTO profiles (email, profile_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at",
                (email, json.dumps(prof), time.time()))
    db.commit()

def load_profile(email: str) -> Dict[str, Any]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT profile_json FROM profiles WHERE email=?", (email,))
    r = cur.fetchone()
    return json.loads(r["profile_json"]) if r else {}

def ensure_session(session_id: str, email: str, persona_id: str = "chip"):
    db = _connect(); cur = db.cursor()
    cur.execute("INSERT OR IGNORE INTO sessions (id, email, persona_id, started_at, ended_at, summary_json) VALUES (?,?,?,?,?,?)",
                (session_id, email, persona_id, time.time(), None, "{}"))
    db.commit()

def add_message(session_id: str, role: str, text: str, meta: Dict[str, Any] = None):
    db = _connect(); cur = db.cursor()
    cur.execute("INSERT INTO messages (session_id, role, text, meta_json, created_at) VALUES (?,?,?,?,?)",
                (session_id, role, text, json.dumps(meta or {}), time.time()))
    db.commit()

def list_users() -> List[Dict[str, Any]]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT email, name, title, region, created_at, last_seen FROM users ORDER BY last_seen DESC")
    return [dict(r) for r in cur.fetchall()]

def list_sessions(email: str = None) -> List[Dict[str, Any]]:
    db = _connect(); cur = db.cursor()
    if email:
        cur.execute("SELECT id, email, persona_id, started_at, ended_at, summary_json FROM sessions WHERE email=? ORDER BY started_at DESC", (email,))
    else:
        cur.execute("SELECT id, email, persona_id, started_at, ended_at, summary_json FROM sessions ORDER BY started_at DESC")
    out = []
    for r in cur.fetchall():
        out.append({"id": r["id"], "email": r["email"], "persona_id": r["persona_id"],
                    "started_at": r["started_at"], "ended_at": r["ended_at"],
                    "summary": json.loads(r["summary_json"] or "{}")})
    return out

def list_messages(session_id: str) -> List[Dict[str, Any]]:
    db = _connect(); cur = db.cursor()
    cur.execute("SELECT role, text, created_at FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,))
    return [dict(r) for r in cur.fetchall()]

def anonymize_session(session_id: str):
    db = _connect(); cur = db.cursor()
    # remove linkage to user email in sessions
    cur.execute("UPDATE sessions SET email='anonymized@example.com' WHERE id=?", (session_id,))
    db.commit()

def load_all_into_memory(memory: Dict[str, Any]):
    # Load config (latest)
    try:
        cfg = latest_config()
        if cfg:
            memory.setdefault('configs', {}).update(cfg)
    except Exception:
        pass
    # Load latest layout per breakpoint
    try:
        db = _connect(); cur = db.cursor()
        cur.execute("SELECT breakpoint, MAX(version) AS mv FROM layouts GROUP BY breakpoint")
        for row in cur.fetchall():
            bp = row["breakpoint"]; mv = int(row["mv"] or 0)
            if mv:
                memory.setdefault('layouts', {})[bp] = {"version": mv, "state": get_layout(bp, mv)}
    except Exception:
        pass