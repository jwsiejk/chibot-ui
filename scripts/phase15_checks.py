import os, json, sqlite3, importlib, sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.db_dal import DAL, DBConfig, health_check
from app.api_v1.admin import bp as admin_bp

def run():
    # 1) Spin migrations (sqlite) and assert indexes present
    os.environ["SQLITE_DB"] = str(BASE / "ci_phase15.sqlite3")
    os.system(f"{sys.executable} {BASE / 'scripts' / 'migrate_sqlite_ci.py'}")
    db = str(BASE / "ci_phase15.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, title TEXT, region TEXT)")
    conn.commit()
    # check indexes via pragma (sqlite shows indexes by name)
    idxs = [r[1] for r in conn.execute("PRAGMA index_list('sessions')")]
    assert "idx_sessions_started_at" in idxs
    assert "idx_sessions_email" in idxs
    idxm = [r[1] for r in conn.execute("PRAGMA index_list('messages')")]
    assert "idx_messages_created_at" in idxm
    assert "idx_messages_session" in idxm
    # outbox presence
    conn.execute("INSERT OR IGNORE INTO outbox (id, kind, payload_json, status) VALUES ('1','transcript_email','{}','queued')")
    conn.commit()
    # 2) Retention: anonymize/delete no-op tests
    conn.execute("INSERT OR REPLACE INTO users (email, name, title, region) VALUES ('a@b.com','Alice','PTM','West')")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT, email TEXT, started_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT, session_id TEXT, text TEXT, created_at TEXT)")
    conn.execute("INSERT INTO sessions (id,email,started_at) VALUES ('s1','a@b.com','t')")
    conn.execute("INSERT INTO messages (id,session_id,text,created_at) VALUES ('m1','s1','hello','t')")
    conn.commit()

    from app.db_dal import anonymize_user, delete_user_data, DAL, DBConfig
    dal = DAL(DBConfig(url=f"sqlite:///{db}"))
    n = anonymize_user(dal, "a@b.com")
    d = delete_user_data(dal, "a@b.com")
    # Verify user row still exists but name anonymized
    name = conn.execute("SELECT name FROM users WHERE email='a@b.com'").fetchone()[0]
    assert name == "(anonymized)"
    # Sessions/messages deleted
    assert conn.execute("SELECT count(1) FROM sessions WHERE email='a@b.com'").fetchone()[0] == 0
    # 3) Health endpoint (call function directly via DAL)
    h = health_check(dal)
    assert h["ok"] is True
    print("PHASE15: PASS")

if __name__ == "__main__":
    run()
