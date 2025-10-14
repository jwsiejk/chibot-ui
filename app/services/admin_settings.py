# app/services/admin_settings.py
from __future__ import annotations
import os, json, threading
from typing import Any, Dict

_LOCK = threading.Lock()

# ---------------- In-memory defaults (used as base / fallback) ----------------
_settings: Dict[str, Any] = {
    "confirm_ms": 420,
    "echo_threshold_boost": 1.9,
    "language_lock": "en",
    "min_speech_ms": 220,
    "nudge_delay_ms": 4200,
    "nudge_backoff_after_ignored": 2,
    "silence_guard_ms": 1800,
    "max_turn_seconds": 90,

    # Audio feature toggle — default TRUE unless explicitly forced false via env/DB
    "feature_audio": (os.environ.get("FEATURE_AUDIO") or "true").lower() == "true",

    # Manual barge-in (PTT) feature flags
    "feature_manual_barge_in": True,
    "barge_in_mode_manual": True,

    # TTS runtime tunables (non-secret) – keys remain in env only unless user saves
    "tts_voice_id": os.environ.get("ELEVENLABS_VOICE_ID", ""),
    "tts_output_format": os.environ.get("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128"),
    "tts_model_id": os.environ.get("ELEVEN_MODEL_ID", "eleven_multilingual_v2"),
}

# ------------------------- DB helpers (safe best-effort) ----------------------

def _persist_enabled() -> bool:
    try:
        from ..db import persist_enabled
        return bool(persist_enabled())
    except Exception:
        return False

def _db_get_settings() -> Dict[str, Any]:
    """
    Load admin settings from Neon (admin_settings.value_jsonb where key='settings').
    Returns {} if not available. Safe to call even if schema is missing.
    """
    if not _persist_enabled():
        return {}
    try:
        from ..dal import neon_pg as pg
        pg.ensure_schema()
        # Detect dialect to pick placeholder style
        dialect = getattr(pg, "_DIALECT", None) or "sqlite"
        if dialect == "postgresql":
            row = pg._fetch_one("SELECT value_jsonb FROM admin_settings WHERE key=%s", ["settings"])
            if not row:
                return {}
            value = row[0]
        else:
            row = pg._fetch_one("SELECT value_jsonb FROM admin_settings WHERE key=?", ["settings"])
            if not row:
                return {}
            # sqlite row is a Row mapping
            value = row["value_jsonb"]
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "ignore")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                # value_jsonb might already be serialized JSON (driver dependent)
                pass
        if isinstance(value, dict):
            return dict(value)
        # Last resort: attempt json.loads on string repr
        try:
            return json.loads(str(value))
        except Exception:
            return {}
    except Exception:
        return {}

def _db_upsert_settings(new_settings: Dict[str, Any]) -> None:
    """
    Upsert admin settings into Neon; merges with existing row server-side.
    Best-effort (swallows errors).
    """
    if not _persist_enabled():
        return
    try:
        from ..dal import neon_pg as pg
        pg.ensure_schema()
        dialect = getattr(pg, "_DIALECT", None) or "sqlite"

        # Merge with existing row to avoid clobbering keys the UI didn't send
        existing = _db_get_settings()
        merged = dict(existing)
        merged.update(new_settings or {})

        payload = json.dumps(merged)
        if dialect == "postgresql":
            # Standard upsert; keep bookkeeping columns if present
            pg._exec(
                """
                INSERT INTO admin_settings (key, value_jsonb, type, updated_by, updated_at, version)
                VALUES (%s, %s::jsonb, 'config', 'system', now(), 1)
                ON CONFLICT (key) DO UPDATE
                  SET value_jsonb = EXCLUDED.value_jsonb,
                      updated_at  = now(),
                      version     = admin_settings.version + 1
                """,
                ["settings", payload],
                fetch=False
            )
        else:
            # SQLite variant
            pg._exec(
                """
                INSERT INTO admin_settings (key, value_jsonb, type, updated_by, updated_at, version)
                VALUES (?, ?, 'config', 'system', strftime('%s','now'), 1)
                ON CONFLICT(key) DO UPDATE SET
                  value_jsonb = excluded.value_jsonb,
                  updated_at  = strftime('%s','now'),
                  version     = admin_settings.version + 1
                """,
                ["settings", payload],
                fetch=False
            )
    except Exception:
        # Best-effort persistence: ignore DB errors
        pass

# ------------------------------- Public API ----------------------------------

def get_settings() -> Dict[str, Any]:
    """
    Return the effective admin settings:
    - Start from in-memory defaults
    - Overlay DB row (if persistence enabled and row exists)
    """
    base: Dict[str, Any]
    with _LOCK:
        base = dict(_settings)

    db_overlay = _db_get_settings()
    if db_overlay:
        # Only apply known keys to avoid injecting unexpected ones
        for k, v in db_overlay.items():
            if k in base:
                base[k] = v
    return base

def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge the provided keys into the settings (in-memory),
    then persist the merged result to Neon (admin_settings) if available.
    """
    if not isinstance(patch, dict):
        patch = {}

    with _LOCK:
        for k, v in patch.items():
            if k in _settings:
                _settings[k] = v
        effective = dict(_settings)

    # Persist merged to DB (best-effort)
    try:
        _db_upsert_settings(effective)
    except Exception:
        pass

    return effective

def vendor_status() -> Dict[str, Any]:
    # Never expose secrets; just indicate presence
    return {
        "llm": "OpenAI",
        "stt": "Deepgram",
        "tts": {
            "provider": "ElevenLabs",
            "key_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "voice_id_set": bool(os.environ.get("ELEVENLABS_VOICE_ID")),
            "output_format": os.environ.get("ELEVEN_OUTPUT_FORMAT", "opus_24000"),
        }
    }
