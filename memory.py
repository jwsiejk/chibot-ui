
# memory.py — DB helpers for Ask Chip (Neon/Postgres)
from __future__ import annotations
import os, json, time
from typing import List, Dict, Any, Optional, Tuple
import psycopg
from psycopg.rows import dict_row

# ---------- Connection helpers ----------
def _dsn_from_env() -> Optional[str]:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return None
    url = "".join(url.split())  # strip whitespace
    if "sslmode=" not in url:
        url = url + ("&sslmode=require" if "?" in url else "?sslmode=require")
    return url

def get_connection():
    dsn = _dsn_from_env()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(dsn, row_factory=dict_row)

# ---------- Schema bootstrap ----------
DDL = [
    # minimal user profile
    """    CREATE TABLE IF NOT EXISTS public.users (
        email       text PRIMARY KEY,
        name        text,
        title       text,
        region      text,
        created_at  timestamptz DEFAULT now()
    );""",

    # conversation logs
    """    CREATE TABLE IF NOT EXISTS public.logs (
        id         bigserial PRIMARY KEY,
        email      text NOT NULL,
        role       text NOT NULL CHECK (role IN ('user','assistant','system')),
        message    text NOT NULL,
        created_at timestamptz DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_logs_email_time ON public.logs(email, created_at DESC);
    """,

    # per-user session summary (rolling short summary)
    """    CREATE TABLE IF NOT EXISTS public.session_summaries (
        email      text PRIMARY KEY,
        summary    text,
        updated_at timestamptz DEFAULT now()
    );""",

    # user preferences (tone/verbosity/channel)
    """    CREATE TABLE IF NOT EXISTS public.user_preferences (
        email      text PRIMARY KEY,
        tone       text DEFAULT 'friendly',
        verbosity  text DEFAULT 'concise',
        channel    text DEFAULT 'web',
        updated_at timestamptz DEFAULT now()
    );""",

    # lightweight long-term notes (no vectors)
    """    CREATE TABLE IF NOT EXISTS public.user_notes (
        id         bigserial PRIMARY KEY,
        email      text NOT NULL,
        topic      text NOT NULL,
        note       text NOT NULL,
        weight     real DEFAULT 0.5,
        created_at timestamptz DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_user_notes_email ON public.user_notes(email);
    CREATE INDEX IF NOT EXISTS idx_user_notes_fts
      ON public.user_notes USING GIN (to_tsvector('english', note));
    """,

    # explicit feedback
    """    CREATE TABLE IF NOT EXISTS public.feedback (
        id         bigserial PRIMARY KEY,
        email      text NOT NULL,
        session_id text,
        message_id text,
        rating     int,
        note       text,
        created_at timestamptz DEFAULT now()
    );""",
]

def init_db() -> bool:
    dsn = _dsn_from_env()
    if not dsn: return False
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        conn.commit()
    return True

# ---------- User profile ----------
def get_user(email: str) -> Optional[Dict[str, Any]]:
    if not email: return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT email, name, title, region FROM public.users WHERE email=%s", (email,))
        row = cur.fetchone()
        return dict(row) if row else None

def save_user(email: str, name: Optional[str]=None, title: Optional[str]=None, region: Optional[str]=None) -> bool:
    if not email: return False
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """            INSERT INTO public.users(email, name, title, region)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                name=EXCLUDED.name,
                title=EXCLUDED.title,
                region=EXCLUDED.region,
                created_at = public.users.created_at
            """ ,
            (email, name, title, region)
        )
        conn.commit()
        return True

# ---------- Conversation logs ----------
def log_conversation(email: str, role: str, message: str) -> None:
    if not email or not role or not message: return
    # basic redaction of emails/phones
    msg = _redact(message)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO public.logs(email, role, message) VALUES (%s,%s,%s)", (email, role, msg))
        conn.commit()

def get_recent_conversation(email: str, limit: int=10) -> List[Dict[str,str]]:
    if not email: return []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """            SELECT role, message
            FROM public.logs
            WHERE email = %s
            ORDER BY created_at DESC
            LIMIT %s
            """, (email, limit)
        )
        rows = cur.fetchall() or []
    rows.reverse()
    return [{"role": r["role"], "message": r["message"]} for r in rows]

# ---------- Session summary ----------
def get_session_summary(email: str) -> Optional[str]:
    if not email: return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT summary FROM public.session_summaries WHERE email=%s", (email,))
        row = cur.fetchone()
        return row["summary"] if row and row.get("summary") else None

def set_session_summary(email: str, summary: str) -> None:
    if not email: return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """            INSERT INTO public.session_summaries(email, summary, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (email) DO UPDATE SET summary=EXCLUDED.summary, updated_at=now()
            """, (email, summary)
        )
        conn.commit()

# ---------- Preferences & notes ----------
def get_preferences(email: str) -> Dict[str, Any]:
    if not email: return {"tone":"friendly","verbosity":"concise","channel":"web"}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT tone, verbosity, channel FROM public.user_preferences WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row:
            return {"tone":"friendly","verbosity":"concise","channel":"web"}
        return dict(row)

def save_preferences(email: str, **kwargs) -> None:
    if not email: return
    tone = kwargs.get("tone")
    verbosity = kwargs.get("verbosity")
    channel = kwargs.get("channel")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """            INSERT INTO public.user_preferences(email, tone, verbosity, channel, updated_at)
            VALUES (%s,%s,%s,%s, now())
            ON CONFLICT (email) DO UPDATE SET
              tone=COALESCE(EXCLUDED.tone, public.user_preferences.tone),
              verbosity=COALESCE(EXCLUDED.verbosity, public.user_preferences.verbosity),
              channel=COALESCE(EXCLUDED.channel, public.user_preferences.channel),
              updated_at=now()
            """,
            (email, tone, verbosity, channel)
        )
        conn.commit()

def remember(email: str, topic: str, note: str, weight: float=0.5) -> None:
    if not email or not topic or not note: return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.user_notes(email, topic, note, weight) VALUES (%s,%s,%s,%s)",
            (email, topic, note, float(weight))
        )
        conn.commit()

def recall_notes(email: str, query: str, k: int=5) -> List[str]:
    if not email or not query: return []
    with get_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """                SELECT note FROM public.user_notes
                WHERE email=%s AND to_tsvector('english', note) @@ plainto_tsquery('english', %s)
                ORDER BY weight DESC, created_at DESC
                LIMIT %s
                """, (email, query, k)
            )
        except Exception:
            # Fallback to ILIKE if FTS extension not available
            cur.execute(
                """                SELECT note FROM public.user_notes
                WHERE email=%s AND note ILIKE %s
                ORDER BY weight DESC, created_at DESC
                LIMIT %s
                """, (email, f"%{query}%", k)
            )
        rows = cur.fetchall() or []
        return [r["note"] for r in rows]

# ---------- Helpers ----------
def _redact(text: str) -> str:
    if not text: return text
    import re
    s = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", text)
    s = re.sub(r"\b\+?\d[\d\s\-()]{6,}\b", "[phone]", s)
    return s
