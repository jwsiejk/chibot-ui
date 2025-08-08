import os
import psycopg2
import traceback

# Strip whitespace/newlines from DATABASE_URL
DB_URL = os.getenv("DATABASE_URL", "").strip()

def get_connection():
    try:
        print("🔌 Connecting to DB using full DATABASE_URL...")
        return psycopg2.connect(DB_URL)
    except Exception as e:
        print("❌ Failed to connect to database:")
        traceback.print_exc()
        raise

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            login VARCHAR(255) UNIQUE NOT NULL,
            profile JSONB
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_user(login):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT profile FROM users WHERE login = %s", (login,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def save_user(login, profile):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (login, profile)
        VALUES (%s, %s)
        ON CONFLICT (login) DO UPDATE SET profile = EXCLUDED.profile
    """, (login, profile))
    conn.commit()
    cur.close()
    conn.close()

def log_conversation(login, user_msg, chip_reply):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            login VARCHAR(255),
            user_msg TEXT,
            chip_reply TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        INSERT INTO messages (login, user_msg, chip_reply)
        VALUES (%s, %s, %s)
    """, (login, user_msg, chip_reply))
    conn.commit()
    cur.close()
    conn.close()
