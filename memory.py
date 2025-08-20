import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

def _dsn_from_env():
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    url = "".join(url.split())
    if "sslmode=" not in url:
        if "?" in url:
            url += "&sslmode=require"
        else:
            url += "?sslmode=require"
    return url

def get_connection():
    dsn = _dsn_from_env()
    if not dsn:
        return None
    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)

def init_db():
    conn = get_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""                        CREATE TABLE IF NOT EXISTS public.users (
                        email TEXT PRIMARY KEY,
                        name TEXT,
                        title TEXT,
                        region TEXT,
                        profile JSONB,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        updated_at TIMESTAMPTZ DEFAULT now()
                    );
                """ )
                cur.execute("""                        CREATE TABLE IF NOT EXISTS public.logs (
                        id BIGSERIAL PRIMARY KEY,
                        email TEXT,
                        role TEXT,
                        message TEXT,
                        created_at TIMESTAMPTZ DEFAULT now()
                    );
                """ )
    finally:
        conn.close()

def get_user(email: str):
    conn = get_connection()
    if not conn:
        return {"email": email}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT email, name, title, region, profile FROM public.users WHERE email = %s", (email,))
                row = cur.fetchone()
                if not row:
                    return None
                return dict(row)
    finally:
        conn.close()

def save_user(email: str, name=None, title=None, region=None, profile=None):
    conn = get_connection()
    if not conn:
        return
    profile_json = json.dumps(profile) if profile is not None else None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""                        INSERT INTO public.users (email, name, title, region, profile)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (email) DO UPDATE SET
                        name = EXCLUDED.name,
                        title = EXCLUDED.title,
                        region = EXCLUDED.region,
                        profile = COALESCE(public.users.profile, '{}'::jsonb) || COALESCE(EXCLUDED.profile, '{}'::jsonb),
                        updated_at = now();
                """, (email, name, title, region, profile_json))
    finally:
        conn.close()

def log_conversation(email: str, role: str, message: str):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.logs (email, role, message) VALUES (%s, %s, %s)",
                    (email, role, message)
                )
    finally:
        conn.close()

def get_recent_messages(email: str, limit: int = 10):
    conn = get_connection()
    if not conn:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, message FROM public.logs WHERE email = %s ORDER BY id DESC LIMIT %s",
                    (email, limit)
                )
                rows = cur.fetchall() or []
                rows.reverse()
                return [{"role": r["role"], "message": r["message"]} for r in rows]
    finally:
        conn.close()

def get_recent_conversation(email: str, limit: int = 8):
    conn = get_connection()
    if not conn:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, message, created_at FROM public.logs WHERE email = %s ORDER BY created_at DESC LIMIT %s",
                    (email, limit)
                )
                rows = cur.fetchall() or []
                # return in chronological order
                rows = list(reversed(rows))
                return [{"role": r["role"], "message": r["message"]} for r in rows]
    finally:
        conn.close()
