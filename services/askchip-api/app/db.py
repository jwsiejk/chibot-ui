from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from app.models import EventRecord, MessageRecord, SessionRecord, TimingRecord


class DatabaseError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                '''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    turn_id TEXT,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS timings (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    turn_id TEXT,
                    phase TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_ms INTEGER,
                    meta TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                '''
            )

    def upsert_setting(self, key: str, value: str, updated_at: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                '''
                INSERT INTO settings(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                ''',
                (key, value, updated_at),
            )

    def create_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                'INSERT INTO sessions(id, title, created_at, updated_at, last_message_at) VALUES(?, ?, ?, ?, ?)',
                (session.id, session.title, session.created_at.isoformat(), session.updated_at.isoformat(), None),
            )
        return session

    def list_sessions(self) -> list[SessionRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                'SELECT id, title, created_at, updated_at, last_message_at FROM sessions ORDER BY updated_at DESC'
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                'SELECT id, title, created_at, updated_at, last_message_at FROM sessions WHERE id = ?', (session_id,)
            ).fetchone()
        return self._session_from_row(row) if row else None

    def rename_session(self, session_id: str, title: str, updated_at: str) -> SessionRecord | None:
        with self._lock, self.connect() as conn:
            conn.execute('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?', (title, updated_at, session_id))
        return self.get_session(session_id)

    def create_message(self, message: MessageRecord) -> MessageRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                '''
                INSERT INTO messages(id, session_id, role, content, status, turn_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.status,
                    message.turn_id,
                    message.created_at.isoformat(),
                    message.updated_at.isoformat(),
                ),
            )
            conn.execute(
                'UPDATE sessions SET updated_at = ?, last_message_at = ? WHERE id = ?',
                (message.updated_at.isoformat(), message.updated_at.isoformat(), message.session_id),
            )
        return message

    def update_message_content(self, message_id: str, content: str, status: str, updated_at: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                'UPDATE messages SET content = ?, status = ?, updated_at = ? WHERE id = ?',
                (content, status, updated_at, message_id),
            )

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                '''SELECT id, session_id, role, content, status, turn_id, created_at, updated_at
                   FROM messages WHERE session_id = ? ORDER BY created_at ASC''',
                (session_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def create_event(self, event: EventRecord) -> EventRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                'INSERT INTO events(id, session_id, turn_id, type, payload, created_at) VALUES(?, ?, ?, ?, ?, ?)',
                (event.id, event.session_id, event.turn_id, event.type, json.dumps(event.payload), event.created_at.isoformat()),
            )
        return event

    def list_events(self, session_id: str) -> list[EventRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                'SELECT id, session_id, turn_id, type, payload, created_at FROM events WHERE session_id = ? ORDER BY created_at ASC',
                (session_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def create_timing(self, timing: TimingRecord) -> TimingRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                '''INSERT INTO timings(id, session_id, turn_id, phase, started_at, ended_at, duration_ms, meta)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    timing.id,
                    timing.session_id,
                    timing.turn_id,
                    timing.phase,
                    timing.started_at.isoformat(),
                    timing.ended_at.isoformat() if timing.ended_at else None,
                    timing.duration_ms,
                    json.dumps(timing.meta),
                ),
            )
        return timing

    def update_timing(self, timing_id: str, ended_at: str, duration_ms: int, meta: dict[str, Any]) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                'UPDATE timings SET ended_at = ?, duration_ms = ?, meta = ? WHERE id = ?',
                (ended_at, duration_ms, json.dumps(meta), timing_id),
            )

    def list_timings(self, session_id: str) -> list[TimingRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                '''SELECT id, session_id, turn_id, phase, started_at, ended_at, duration_ms, meta
                   FROM timings WHERE session_id = ? ORDER BY started_at ASC''',
                (session_id,),
            ).fetchall()
        return [self._timing_from_row(row) for row in rows]

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row['id'],
            title=row['title'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            last_message_at=datetime.fromisoformat(row['last_message_at']) if row['last_message_at'] else None,
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row['id'],
            session_id=row['session_id'],
            role=row['role'],
            content=row['content'],
            status=row['status'],
            turn_id=row['turn_id'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=row['id'],
            session_id=row['session_id'],
            turn_id=row['turn_id'],
            type=row['type'],
            payload=json.loads(row['payload']),
            created_at=datetime.fromisoformat(row['created_at']),
        )

    @staticmethod
    def _timing_from_row(row: sqlite3.Row) -> TimingRecord:
        return TimingRecord(
            id=row['id'],
            session_id=row['session_id'],
            turn_id=row['turn_id'],
            phase=row['phase'],
            started_at=datetime.fromisoformat(row['started_at']),
            ended_at=datetime.fromisoformat(row['ended_at']) if row['ended_at'] else None,
            duration_ms=row['duration_ms'],
            meta=json.loads(row['meta']),
        )
