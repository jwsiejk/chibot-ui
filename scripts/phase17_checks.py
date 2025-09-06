import os, sys, sqlite3, json, time
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.services.mailer import queue_transcript_email
from app.services import outbox

def run():
    db = str(BASE / "ci_phase15.sqlite3")
    os.environ["DATABASE_URL"] = f"sqlite:///{db}"
    # prep schema
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT, session_id TEXT, ended_at TEXT, payload_json TEXT, status TEXT, attempts INTEGER, next_attempt_at TEXT, last_error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    # enqueue N with dedupe
    id1 = queue_transcript_email("S1", "2025-09-04T00:00:00Z", "a@b.com", "subj", "body")
    id2 = queue_transcript_email("S1", "2025-09-04T00:00:00Z", "a@b.com", "subj", "body")  # dedupe
    assert id1 == id2, "Deduplication failed"
    # simulate transient failures then success
    os.environ["SMTP_FAILS_FOR_TEST"] = "2"
    processed = outbox.process_once(limit=5)
    # after first run, should be 0 (fail twice sets next_attempt_at)
    assert processed in (0,1), "Unexpected processed count in first pass"
    # simulate time passing: clear next_attempt_at to force retry now
    conn.execute("UPDATE outbox SET next_attempt_at=NULL WHERE status='queued'")
    conn.commit()
    processed2 = outbox.process_once(limit=5)
    # Now should eventually succeed (after fail count exhausted)
    # process until status is 'sent'
    for _ in range(5):
        conn.execute("UPDATE outbox SET next_attempt_at=NULL WHERE status='queued'")
        conn.commit()
        if outbox.process_once(limit=5)>0:
            break
    status = conn.execute("SELECT status FROM outbox WHERE id=?", (id1,)).fetchone()[0]
    assert status == "sent", f"Outbox item not sent, status={status}"
    print("PHASE17: PASS")

if __name__ == "__main__":
    run()
