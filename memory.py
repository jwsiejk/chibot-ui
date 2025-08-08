print("🔧 Initializing memory module...")

import psycopg2
import os
from urllib.parse import urlparse
from datetime import datetime
import json
import traceback

# Parse the DATABASE_URL safely
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    print("❌ DATABASE_URL not set. Please set it in your environment.")
else:
    print("✅ DATABASE_URL found.")

def connect():
    try:
        result = urlparse(DB_URL)
        print(f"🔌 Connecting to DB at {result.hostname}:{result.port}...")
        return psycopg2.connect(
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port
            sslmode='require'
        )
    except Exception as e:
        print("❌ Failed to connect to database:")
        traceback.print_exc()
        raise

def init_db():
    try:
        print("🛠 Creating tables if not present...")
        with connect() as conn:
            with conn.cursor() as cursor:
                # Updated unified user_profiles table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        login_name TEXT PRIMARY KEY,
                        full_name TEXT NOT NULL,
                        title TEXT NOT NULL,
                        region TEXT DEFAULT 'NA',
                        messages JSONB DEFAULT '[]',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS conversation_history (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        user_message TEXT,
                        chip_response TEXT
                    )
                ''')
                conn.commit()
        print("✅ Tables are ready.")
    except Exception as e:
        print("❌ Error during init_db():")
        traceback.print_exc()
        raise

def get_user(user_id):
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM user_profiles WHERE login_name = %s', (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "name": row[1],
                    "role": row[2],
                    "region": row[3],
                    "messages": row[4] if isinstance(row[4], list) else json.loads(row[4]),
                    "created_at": row[5],
                    "updated_at": row[6]
                }
            return None

def save_user(user_id, messages, role, region, name="User"):
    with connect() as conn:
        with conn.cursor() as cursor:
            now = datetime.utcnow()
            messages_json = messages if isinstance(messages, str) else json.dumps(messages)
            cursor.execute('''
                INSERT INTO user_profiles (login_name, full_name, title, region, messages, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (login_name) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    title = EXCLUDED.title,
                    region = EXCLUDED.region,
                    messages = EXCLUDED.messages,
                    updated_at = EXCLUDED.updated_at
            ''', (user_id, name, role, region, messages_json, now))
            conn.commit()

def log_conversation(user_id, user_message, chip_response):
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO conversation_history (user_id, user_message, chip_response)
                VALUES (%s, %s, %s)
            ''', (user_id, user_message, chip_response))
            conn.commit()
