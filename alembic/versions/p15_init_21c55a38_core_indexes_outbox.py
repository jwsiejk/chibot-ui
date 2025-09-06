from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "p15_init_21c55a38"
down_revision = None

def upgrade() -> None:
    # Core tables may already exist; use IF NOT EXISTS patterns where supported.
    # For Alembic runtime on Postgres; for CI we provide a lightweight runner.
    op.execute("""
    CREATE TABLE IF NOT EXISTS schema_version (
        id INTEGER PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );""")
    -- sessions indexes
    op.execute("""CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at);""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions (email);""")
    -- messages indexes
    op.execute("""CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages (created_at);""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id);""")
    -- logs index
    op.execute("""CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs (created_at);""")
    -- outbox table for email/transcripts (phase17 uses)
    op.execute("""
    CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        session_id TEXT,
        ended_at TIMESTAMP,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMP,
        last_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );""")
    -- idempotency on (session_id, ended_at) for transcript emails
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_transcript_dedupe ON outbox (session_id, ended_at) WHERE kind='transcript_email';""")

def downgrade() -> None:
    # Keep objects for safety; do not drop automatically.
    pass
