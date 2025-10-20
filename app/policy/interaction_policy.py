from __future__ import annotations

from typing import Dict, Mapping

MANUAL_ONLY_DURING_TTS = "manual_only_during_tts"
AUTO_VAD_READY = "auto_vad_ready"

_BASE_POLICIES: Dict[str, Dict[str, object]] = {
    MANUAL_ONLY_DURING_TTS: {
        "mode": MANUAL_ONLY_DURING_TTS,
        "allow_auto_vad": False,
        "auto_commit_when_ready": False,
        "allow_ptt_barge": True,
        "suppress_vad_during_tts": True,
    },
    AUTO_VAD_READY: {
        "mode": AUTO_VAD_READY,
        "allow_auto_vad": True,
        "auto_commit_when_ready": False,
        "allow_ptt_barge": True,
        "suppress_vad_during_tts": False,
    },
}


def _snapshot(mode: str, overrides: Mapping[str, object] | None = None) -> Dict[str, object]:
    base = dict(_BASE_POLICIES[mode])
    if overrides:
        for key, value in overrides.items():
            base[key] = value
    return base


def _apply_inputs(
    *,
    auto_commit_when_ready: bool | None = None,
    allow_ptt_barge: bool | None = None,
    allow_auto_vad: bool | None = None,
    overrides: Mapping[str, object] | None = None,
) -> Dict[str, object]:
    payload: Dict[str, object] = {}
    if auto_commit_when_ready is not None:
        payload["auto_commit_when_ready"] = bool(auto_commit_when_ready)
    if allow_ptt_barge is not None:
        payload["allow_ptt_barge"] = bool(allow_ptt_barge)
    if allow_auto_vad is not None:
        payload["allow_auto_vad"] = bool(allow_auto_vad)
    if overrides:
        for key, value in overrides.items():
            payload[key] = value
    return payload


def for_tts(
    overrides: Mapping[str, object] | None = None,
    *,
    auto_commit_when_ready: bool | None = None,
    allow_ptt_barge: bool | None = None,
) -> Dict[str, object]:
    """Snapshot used while assistant audio or confirmations are active."""

    return _snapshot(
        MANUAL_ONLY_DURING_TTS,
        _apply_inputs(
            auto_commit_when_ready=auto_commit_when_ready,
            allow_ptt_barge=allow_ptt_barge,
            overrides=overrides,
        ),
    )


def for_idle(
    overrides: Mapping[str, object] | None = None,
    *,
    auto_commit_when_ready: bool | None = None,
    allow_ptt_barge: bool | None = None,
    allow_auto_vad: bool | None = None,
) -> Dict[str, object]:
    """Snapshot used when the client is idle and auto VAD is allowed."""

    return _snapshot(
        AUTO_VAD_READY,
        _apply_inputs(
            auto_commit_when_ready=auto_commit_when_ready,
            allow_ptt_barge=allow_ptt_barge,
            allow_auto_vad=allow_auto_vad,
            overrides=overrides,
        ),
    )
