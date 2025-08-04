
import sqlite3
from datetime import datetime

DB_PATH = 'chip_memory.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            role TEXT,
            region TEXT,
            preferences TEXT,
            last_interaction TEXT,
            notes TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_message TEXT,
            chip_response TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM user_memory WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def save_user(user_id, name, role, region):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute('''
        INSERT INTO user_memory (user_id, name, role, region, last_interaction)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name=excluded.name,
            role=excluded.role,
            region=excluded.region,
            last_interaction=excluded.last_interaction
    ''', (user_id, name, role, region, now))
    conn.commit()
    conn.close()

def log_conversation(user_id, user_message, chip_response):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversation_history (user_id, user_message, chip_response)
        VALUES (?, ?, ?)
    ''', (user_id, user_message, chip_response))
    conn.commit()
    conn.close()
