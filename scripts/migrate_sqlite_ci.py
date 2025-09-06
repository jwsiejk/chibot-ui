import os, sqlite3, sys, time, re, json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
VERS = BASE / "alembic" / "versions"
DB = os.environ.get("SQLITE_DB", str(BASE / "ci_phase15.sqlite3"))

def ensure_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT, email TEXT, started_at TEXT, ended_at TEXT, summary_jsonb TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT, session_id TEXT, role TEXT, text TEXT, meta_jsonb TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS logs (id TEXT, email TEXT, role TEXT, message TEXT, created_at TEXT)")
    conn.commit()

def run():
    conn = sqlite3.connect(DB)
    ensure_tables(conn)
    # apply index creation and outbox table etc.
    # emulate what's in Alembic version file
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions (email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages (created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs (created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        session_id TEXT,
        ended_at TEXT,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_transcript_dedupe ON outbox (session_id, ended_at)")
    conn.commit()
    print("OK: migrations (sqlite) applied to", DB)

if __name__ == "__main__":
    run()
