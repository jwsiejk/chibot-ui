# memory.py – Handles user profile and session data in Neon Postgres
#
# Notes:
# - DB is the source of truth. A BEFORE INSERT/UPDATE trigger should set
#   users.login and users.domain from users.email, so this file only writes
#   email, name, title.
# - Works against both a minimal "users(email,name,title)" table and a richer
#   schema with id/login/domain/created_at/updated_at.

import os
import psycopg2

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
    Always open a fresh connection using a normalized DATABASE_URL.
    Do NOT cache the URL at import time.
    """
    url = _normalize(os.getenv("DATABASE_URL"))
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)

def get_user(email):
    """
    Return a dict with name/title if the user exists; else None.
    (Kept minimal to be compatible with both schemas.)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, title FROM public.users WHERE email = %s",
                (email,)
            )
            row = cur.fetchone()
            if row:
                return {"name": row[0] or "", "title": row[1] or ""}
            return None

def save_user(email, name_or_profile, title=None, *_, **__):
    """
    Upsert user by email with only name/title.
    - If called as save_user(email, name, title) → uses those.
    - If called as save_user(email, profile_dict) → uses profile_dict['name']/['title']
      (falls back from 'role' to 'title' if needed for backward compatibility).
    Returns a small dict {email, name, title}.
    """
    if not email:
        raise ValueError("email is required")

    # Support both call styles without breaking older code
    if isinstance(name_or_profile, dict):
        name  = (name_or_profile.get("name") or "").strip()
        # Accept legacy 'role' key as a fallback for title
        _t    = name_or_profile.get("title")
        title = (_t if _t is not None else name_or_profile.get("role") or "").strip()
    else:
        name  = (name_or_profile or "").strip()
        title = (title or "").strip()

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Only touch columns guaranteed to exist in both schemas.
            cur.execute(
                """
                INSERT INTO public.users (email, name, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO UPDATE
                  SET name  = EXCLUDED.name,
                      title = EXCLUDED.title
                RETURNING email, name, title
                """,
                (email, name, title)
            )
            row = cur.fetchone()
            conn.commit()
            # Return a small, stable dict
            return {"email": row[0], "name": row[1] or "", "title": row[2] or ""}

def log_conversation(email, transcript, response):
    """
    Append a log row for observability.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.logs (email, transcript, response) VALUES (%s, %s, %s)",
                (email, transcript, response)
            )
            conn.commit()

# Optional: lightweight initializer (safe to call at startup)
def init_db():
    """
    Create minimal tables if they don't exist (non-destructive).
    Only includes columns used by the functions above.
    This won't modify an existing richer schema.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.users (
                    email TEXT PRIMARY KEY,
                    name  TEXT,
                    title TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.logs (
                    id SERIAL PRIMARY KEY,
                    email TEXT,
                    transcript TEXT,
                    response TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
