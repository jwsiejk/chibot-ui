# memory.py – Handles user profile and session data in Neon Postgres
#
# Permanent-safe profile save:
# - DB trigger fills users.login/domain from email (set earlier in SQL).
# - This file upserts only email, name, title — and ALSO stores/merges
#   a users.profile JSONB so /api/me checks see a complete profile.

import os
import psycopg2
import psycopg2.extras as extras

def _normalize(url: str) -> str:
    """
    Clean DATABASE_URL by stripping wrapping quotes and removing stray
    whitespace/newlines that can break sslmode (e.g., 'require\\n').
    """
    url = (url or "").strip()
    if (url.startswith('"') and url.endswith('"')) or (url.startswith("'") and url.endswith("'")):
        url = url[1:-1].strip()
    return url.replace("\n", "").replace("\r", "").replace(" ", "")

def get_connection():
    """
    Open a fresh connection using a normalized DATABASE_URL.
    """
    url = _normalize(os.getenv("DATABASE_URL"))
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # RealDictCursor is handy if you later want dict rows
    return psycopg2.connect(url, cursor_factory=extras.RealDictCursor)

def get_user(email):
    """
    Return minimal profile info for the given email, or None.
    Looks at columns first; falls back to JSONB if present.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              email,
              COALESCE(name,  profile->>'name')  AS name,
              COALESCE(title, profile->>'title') AS title
            FROM public.users
            WHERE email = %s
            """,
            (email,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"email": row["email"], "name": row["name"] or "", "title": row["title"] or ""}

def save_user(email, name_or_profile, title=None, *_, **__):
    """
    Upsert user by email with only name/title, and persist a profile JSONB:
      profile := {name, title, email}
    Supports both call styles:
      save_user(email, "Full Name", "Title")
      save_user(email, {"name": "...", "title": "..."})
    Returns {email, name, title}.
    """
    if not email:
        raise ValueError("email is required")

    # Accept both call styles
    if isinstance(name_or_profile, dict):
        name  = (name_or_profile.get("name") or "").strip()
        _t    = name_or_profile.get("title")
        title = (_t if _t is not None else name_or_profile.get("role") or "").strip()  # legacy 'role' fallback
    else:
        name  = (name_or_profile or "").strip()
        title = (title or "").strip()

    profile_doc = {"email": email, "name": name, "title": title}

    with get_connection() as conn, conn.cursor() as cur:
        # Keep the SQL tolerant: rely on UNIQUE(email) for ON CONFLICT; if absent, you can add it in DB.
        cur.execute(
            """
            INSERT INTO public.users (email, name, title, profile, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE
              SET name    = EXCLUDED.name,
                  title   = EXCLUDED.title,
                  profile = COALESCE(public.users.profile, '{}'::jsonb) || EXCLUDED.profile,
                  updated_at = NOW()
            RETURNING email, name, title
            """,
            (email, name, title, extras.Json(profile_doc))
        )
        row = cur.fetchone()
        conn.commit()
        return {"email": row["email"], "name": row["name"] or "", "title": row["title"] or ""}

def ensure_logs_table():
    """
    Create the logs table if it doesn't exist.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.logs (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                transcript TEXT,
                response TEXT,
                meta JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()

def log_conversation(email, transcript, response):
    """
    Append a log row for observability.
    Ensures the logs table exists; if insert fails, prints warning instead of crashing.
    """
    try:
        ensure_logs_table()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.logs (email, transcript, response) VALUES (%s, %s, %s)",
                (email, transcript, response)
            )
            conn.commit()
    except Exception as e:
        print("⚠️ log_conversation failed:", e)

def init_db():
    """
    Create minimal tables if they don't exist (non-destructive).
    This won't modify an existing richer schema.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.users (
                email TEXT PRIMARY KEY,
                name  TEXT,
                title TEXT,
                profile JSONB
            )
        """)
        ensure_logs_table()
        conn.commit()
