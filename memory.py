
import psycopg2
import os
from urllib.parse import urlparse
from datetime import datetime

# Parse the DATABASE_URL safely
DB_URL = os.environ.get("DATABASE_URL")

def connect():
    result = urlparse(DB_URL)
    return psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )

def init_db():
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_memory (
                    user_id TEXT PRIMARY KEY,
                    name TEXT,
                    role TEXT,
                    region TEXT,
                    preferences TEXT,
                    last_interaction TIMESTAMP,
                    notes TEXT
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

def get_user(user_id):
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM user_memory WHERE user_id = %s', (user_id,))
            return cursor.fetchone()

def save_user(user_id, name, role, region):
    with connect() as conn:
        with conn.cursor() as cursor:
            now = datetime.utcnow()
            cursor.execute('''
                INSERT INTO user_memory (user_id, name, role, region, last_interaction)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    role = EXCLUDED.role,
                    region = EXCLUDED.region,
                    last_interaction = EXCLUDED.last_interaction
            ''', (user_id, name, role, region, now))
            conn.commit()

def log_conversation(user_id, user_message, chip_response):
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO conversation_history (user_id, user_message, chip_response)
                VALUES (%s, %s, %s)
            ''', (user_id, user_message, chip_response))
            conn.commit()
