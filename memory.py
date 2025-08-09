# memory.py – Handles user profile and session data in Neon Postgres

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
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, title FROM users WHERE email = %s",
                (email,)
            )
            row = cur.fetchone()
            if row:
                return {"name": row[0], "title": row[1]}
            return None

def save_user(email, name_or_profile, title=None, *_, **__):
    """
    Upsert user name/title.
    - If called as save_user(email, name, title) → uses those.
    - If called as save_user(email, profile_dict) → uses profile_dict['name']/['title'] when present.
    Any extra args are ignored for backward compatibility.
    """
    if isinstance(name_or_profile, dict):
        name = (name_or_profile.get("name") or "").strip()
        title = (name_or_profile.get("title") or name_or_profile.get("role") or "").strip()
    else:
        name = (name_or_profile or "").strip()
        title = (title or "").strip()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, name, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO UPDATE
                SET name = EXCLUDED.name,
                    title = EXCLUDED.title
                """,
                (email, name, title)
            )
            conn.commit()

def log_conversation(email, transcript, response):
    """
    Append a log row for observability.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO logs (email, transcript, response) VALUES (%s, %s, %s)",
                (email, transcript, response)
            )
            conn.commit()

# Optional: lightweight initializer (safe to call at startup)
def init_db():
    """
    Create minimal tables if they don't exist (non-destructive).
    Only includes columns used by the functions above.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    name  TEXT,
                    title TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    email TEXT,
                    transcript TEXT,
                    response TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
