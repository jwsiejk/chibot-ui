from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, MutableMapping, MutableSequence, Sequence, Tuple

POLICY_VERSION = 2

# map legacy → new; omit keys to drop
LEGACY_MAP: Mapping[Sequence[str], Sequence[str] | None] = {
    # legacy ASR
    ("asr", "prearm_on_tts_end"): ("asr", "prearm_on_tts_end"),
    ("asr", "start_on_ready"): ("asr", "server_starts_input"),  # legacy name
    # legacy VAD/gating
    ("vad", "auto_vad_active"): None,  # runtime state, not a policy knob
    ("vad", "allow_auto_vad"): ("vad", "sender_gate_on_tts"),  # best fit
    # legacy UI badge behavior
    ("ui", "badge_listen_on_asr"): ("ui", "status", "require_active_turn"),
}

DEPRECATED_KEYS = {
    ("input", "require_hotword_to_start"),
    ("input", "require_user_gesture_first_visit"),  # keep only if UX requires
    ("audio", "pipeline", "mode"),
    ("media", "asr_input"),
}

SAFE_DEFAULTS_V2: dict[str, Any] = {
    "version": POLICY_VERSION,
    "asr": {
        "server_starts_input": True,
        "prearm_on_tts_end": True,
        "cold_start_grace_ms": 3000,
    },
    "vad": {
        "warmup_ms": 1500,
        "sender_gate_on_tts": True,
        "client": {
            "enable": True,
            "threshold_dbfs": -60,
            "attack_ms": 80,
            "release_ms": 250,
            "pre_roll_ms": 240,
            "min_active_ms": 300,
        },
    },
    "audio": {
        "start_on_ws_open": False,
        "header_on_first_chunk": True,
        "allow_capture_during_tts": False,
        "keepalive_ms": 1000,
    },
    "watchdog": {
        "partial_wait_ms_first_turn": 3500,
        "partial_wait_ms": 2500,
    },
    "server": {
        "buffer_prestart_audio_ms": 300,
        "sync_ready_on_tts_end": True,
        "asr_ready_deadline_ms": 8000,
        # server_no_speech_timeout_ms should be ≥ 2 × MAX_GATE_SILENCE_MS to let the client close the turn cleanly.
        "no_speech_timeout_ms": 10000,
        "throttle_grace_ms": 2000,
        "queue_pre_ready_audio": True,
        "drop_pre_tts_audio_ms": 0,
    },
    "capture": {
        "mode": "webrtc_aec",  # "webrtc_aec" | "pcm"
        "constraints": {
            "echoCancellation": True,
            "noiseSuppression": True,
            "autoGainControl": False,
            "channelCount": 1,
            "sampleRate": 16000,
        }
    },
    "ui": {
        "status": {
            "require_active_turn": True,
            "enable_meter": True,
        }
    },
}


def _get(d: Mapping[str, Any], path: Sequence[str], default: Any = None) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, Mapping) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _set(d: MutableMapping[str, Any], path: Sequence[str], value: Any) -> None:
    cur: MutableMapping[str, Any] = d
    for key in path[:-1]:
        next_val = cur.get(key)
        if not isinstance(next_val, MutableMapping):
            next_val = {}
            cur[key] = next_val  # type: ignore[assignment]
        cur = next_val  # type: ignore[assignment]
    cur[path[-1]] = value


LegacyHit = Tuple[Tuple[str, ...], Tuple[str, ...]]


def normalize_policy(
    incoming: Mapping[str, Any] | None,
    *,
    legacy_hits: MutableSequence[LegacyHit] | None = None,
) -> dict[str, Any]:
    """Merge SAFE_DEFAULTS_V2 ← incoming (mapped), drop deprecated, prefer v2 keys."""

    src = deepcopy(dict(incoming or {}))
    out = deepcopy(SAFE_DEFAULTS_V2)

    def deep_merge(dst: MutableMapping[str, Any], src_: Mapping[str, Any] | None) -> None:
        for key, value in (src_ or {}).items():
            if isinstance(value, Mapping) and isinstance(dst.get(key), MutableMapping):
                deep_merge(dst[key], value)  # type: ignore[index]
            else:
                dst[key] = deepcopy(value)

    deep_merge(out, {key: value for key, value in src.items() if key in out})

    for legacy_path, new_path in LEGACY_MAP.items():
        val = _get(src, legacy_path, None)
        if val is None or new_path is None:
            continue
        if legacy_path == ("ui", "badge_listen_on_asr"):
            val = not bool(val)
        if legacy_hits is not None:
            legacy_hits.append((tuple(legacy_path), tuple(new_path)))
        if _get(out, new_path, None) is None:
            _set(out, new_path, val)

    for path in DEPRECATED_KEYS:
        parent = _get(src, path[:-1], None)
        if isinstance(parent, MutableMapping) and path[-1] in parent:
            try:
                del parent[path[-1]]
            except Exception:
                pass

    out["version"] = POLICY_VERSION
    out["_normalized_from"] = "v2"
    return out


__all__ = ["POLICY_VERSION", "normalize_policy"]
