# memory.py – Handles user profile and session data in Neon Postgres

import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL)

def get_user(email):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, title FROM users WHERE email = %s", (email,))
            result = cur.fetchone()
            if result:
                return {"name": result[0], "title": result[1]}
            return None

def save_user(email, name, title):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, name, title) VALUES (%s, %s, %s) ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, title = EXCLUDED.title",
                (email, name, title)
            )
            conn.commit()

def log_conversation(email, transcript, response):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO logs (email, transcript, response) VALUES (%s, %s, %s)",
                (email, transcript, response)
            )
            conn.commit()
