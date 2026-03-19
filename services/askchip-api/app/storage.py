from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord


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
                    status TEXT NOT NULL DEFAULT 'ready',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT,
                    active_turn_id TEXT,
                    ready_at TEXT,
                    last_error_at TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    modality TEXT NOT NULL DEFAULT 'text',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    committed_at TEXT,
                    completed_at TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
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
            self._ensure_column(conn, 'sessions', 'status', "TEXT NOT NULL DEFAULT 'ready'")
            self._ensure_column(conn, 'sessions', 'active_turn_id', 'TEXT')
            self._ensure_column(conn, 'sessions', 'ready_at', 'TEXT')
            self._ensure_column(conn, 'sessions', 'last_error_at', 'TEXT')
            self._ensure_column(conn, 'sessions', 'metadata', "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, 'messages', 'source', "TEXT NOT NULL DEFAULT 'user'")
            self._ensure_column(conn, 'messages', 'modality', "TEXT NOT NULL DEFAULT 'text'")
            self._ensure_column(conn, 'messages', 'committed_at', 'TEXT')
            self._ensure_column(conn, 'messages', 'completed_at', 'TEXT')
            self._ensure_column(conn, 'messages', 'metadata', "TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
        if column not in columns:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')

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
                '''INSERT INTO sessions(
                    id, title, status, created_at, updated_at, last_message_at, active_turn_id, ready_at, last_error_at, metadata
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    session.id,
                    session.title,
                    session.status,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.last_message_at.isoformat() if session.last_message_at else None,
                    session.active_turn_id,
                    session.ready_at.isoformat() if session.ready_at else None,
                    session.last_error_at.isoformat() if session.last_error_at else None,
                    json.dumps(session.metadata),
                ),
            )
        return session

    def list_sessions(self) -> list[SessionRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                'SELECT * FROM sessions ORDER BY updated_at DESC'
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock, self.connect() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
        return self._session_from_row(row) if row else None

    def rename_session(self, session_id: str, title: str, updated_at: str) -> SessionRecord | None:
        with self._lock, self.connect() as conn:
            conn.execute('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?', (title, updated_at, session_id))
        return self.get_session(session_id)

    def update_session_state(
        self,
        session_id: str,
        *,
        status: str,
        updated_at: str,
        active_turn_id: str | None = None,
        ready_at: str | None = None,
        last_error_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self.connect() as conn:
            current = conn.execute('SELECT metadata FROM sessions WHERE id = ?', (session_id,)).fetchone()
            merged_metadata = json.loads(current['metadata']) if current and current['metadata'] else {}
            if metadata:
                merged_metadata.update(metadata)
            conn.execute(
                '''UPDATE sessions
                   SET status = ?, updated_at = ?, active_turn_id = ?, ready_at = COALESCE(?, ready_at),
                       last_error_at = COALESCE(?, last_error_at), metadata = ?
                   WHERE id = ?''',
                (status, updated_at, active_turn_id, ready_at, last_error_at, json.dumps(merged_metadata), session_id),
            )

    def create_message(self, message: MessageRecord) -> MessageRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                '''INSERT INTO messages(
                    id, session_id, role, content, status, turn_id, source, modality, created_at, updated_at, committed_at, completed_at, metadata
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.status,
                    message.turn_id,
                    message.source,
                    message.modality,
                    message.created_at.isoformat(),
                    message.updated_at.isoformat(),
                    message.committed_at.isoformat() if message.committed_at else None,
                    message.completed_at.isoformat() if message.completed_at else None,
                    json.dumps(message.metadata),
                ),
            )
            conn.execute(
                'UPDATE sessions SET updated_at = ?, last_message_at = ?, active_turn_id = ? WHERE id = ?',
                (message.updated_at.isoformat(), message.updated_at.isoformat(), message.turn_id, message.session_id),
            )
        return message

    def update_message(
        self,
        message_id: str,
        *,
        content: str,
        status: str,
        updated_at: str,
        committed_at: str | None = None,
        completed_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self.connect() as conn:
            current = conn.execute('SELECT metadata FROM messages WHERE id = ?', (message_id,)).fetchone()
            merged_metadata = json.loads(current['metadata']) if current and current['metadata'] else {}
            if metadata:
                merged_metadata.update(metadata)
            conn.execute(
                '''UPDATE messages
                   SET content = ?, status = ?, updated_at = ?, committed_at = COALESCE(?, committed_at),
                       completed_at = COALESCE(?, completed_at), metadata = ?
                   WHERE id = ?''',
                (content, status, updated_at, committed_at, completed_at, json.dumps(merged_metadata), message_id),
            )

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute('SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC', (session_id,)).fetchall()
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
            rows = conn.execute('SELECT * FROM events WHERE session_id = ? ORDER BY created_at ASC', (session_id,)).fetchall()
        return [self._event_from_row(row) for row in rows]

    def create_timing(self, timing: TimingRecord) -> TimingRecord:
        with self._lock, self.connect() as conn:
            conn.execute(
                'INSERT INTO timings(id, session_id, turn_id, phase, started_at, ended_at, duration_ms, meta) VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
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
            current = conn.execute('SELECT meta FROM timings WHERE id = ?', (timing_id,)).fetchone()
            merged_meta = json.loads(current['meta']) if current and current['meta'] else {}
            merged_meta.update(meta)
            conn.execute('UPDATE timings SET ended_at = ?, duration_ms = ?, meta = ? WHERE id = ?', (ended_at, duration_ms, json.dumps(merged_meta), timing_id))

    def list_timings(self, session_id: str) -> list[TimingRecord]:
        with self._lock, self.connect() as conn:
            rows = conn.execute('SELECT * FROM timings WHERE session_id = ? ORDER BY started_at ASC', (session_id,)).fetchall()
        return [self._timing_from_row(row) for row in rows]

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row['id'],
            title=row['title'],
            status=row['status'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            last_message_at=datetime.fromisoformat(row['last_message_at']) if row['last_message_at'] else None,
            active_turn_id=row['active_turn_id'],
            ready_at=datetime.fromisoformat(row['ready_at']) if row['ready_at'] else None,
            last_error_at=datetime.fromisoformat(row['last_error_at']) if row['last_error_at'] else None,
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
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
            source=row['source'],
            modality=row['modality'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            committed_at=datetime.fromisoformat(row['committed_at']) if row['committed_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
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
