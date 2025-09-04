
import os, sqlite3, json, time
from typing import Optional, Dict, Any

DEFAULT_PATH = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")

SCHEMA = {
    "configs": "CREATE TABLE IF NOT EXISTS configs (k TEXT PRIMARY KEY, v TEXT)",
    "layouts": "CREATE TABLE IF NOT EXISTS layouts (breakpoint TEXT PRIMARY KEY, json TEXT, version INTEGER)",
    "personas": "CREATE TABLE IF NOT EXISTS personas (id TEXT PRIMARY KEY, owner TEXT, published_json TEXT, draft_json TEXT, history_json TEXT, active INTEGER)",
    "sessions": "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, email TEXT, persona_id TEXT, transcript TEXT, updated_at REAL)"
}

def connect(path: Optional[str] = None):
    return sqlite3.connect(path or DEFAULT_PATH)

def migrate(conn):
    cur = conn.cursor()
    for ddl in SCHEMA.values():
        cur.execute(ddl)
    conn.commit()

# --- Configs
def snapshot_configs(conn, cfg: Dict[str, Any]):
    cur = conn.cursor()
    for k, v in cfg.items():
        cur.execute("INSERT INTO configs(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
    conn.commit()

def load_configs(conn) -> Dict[str, Any]:
    out = {}
    for k, v in conn.execute("SELECT k,v FROM configs"):
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return out

# --- Sessions (minimal)
def snapshot_session(conn, sid: str, email: str, persona_id: str, transcript: str):
    conn.execute("INSERT INTO sessions(id,email,persona_id,transcript,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET email=excluded.email, persona_id=excluded.persona_id, transcript=excluded.transcript, updated_at=excluded.updated_at",
                 (sid, email, persona_id, transcript, time.time()))
    conn.commit()

def load_session(conn, sid: str):
    cur = conn.execute("SELECT id,email,persona_id,transcript,updated_at FROM sessions WHERE id=?", (sid,))
    row = cur.fetchone()
    if not row: return None
    return {"id": row[0], "email": row[1], "persona_id": row[2], "transcript": row[3], "updated_at": row[4]}
