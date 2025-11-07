"""Utilities for mapping app policy to Speechmatics parameters."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence

__all__ = ["to_sm_params"]


_SM_AUDIO_FORMAT = {
    "type": "raw",
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
}

_SM_MIN_FINAL_LATENCY_MS = 700
_SM_MAX_FINAL_LATENCY_MS = 4000


_log = logging.getLogger(__name__)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return []


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _coerce_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced


def _coerce_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        coerced = float(value)
    except (TypeError, ValueError):
        return None
    return coerced


def _normalize_language(*candidates: Any) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized:
                return normalized
    return None


def _normalize_punctuation(value: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    bool_candidate = _coerce_bool(value)
    if bool_candidate is not None:
        result["enable_punctuation"] = bool_candidate
        return result

    mapping = _as_mapping(value)
    enabled = _coerce_bool(mapping.get("enabled"))
    if enabled is not None:
        result["enable_punctuation"] = enabled

    overrides_value = mapping.get("overrides")
    permitted_marks: list[str] = []
    sensitivity: float | None = None
    if overrides_value is not None:
        overrides_mapping = _as_mapping(overrides_value)
        if overrides_mapping:
            permitted_value = overrides_mapping.get("permitted_marks")
            for item in _as_sequence(permitted_value):
                if isinstance(item, str):
                    normalized = item.strip()
                    if normalized:
                        permitted_marks.append(normalized)
            sensitivity_value = overrides_mapping.get("sensitivity")
            if isinstance(sensitivity_value, (int, float)):
                clamped = max(0.0, min(1.0, float(sensitivity_value)))
                sensitivity = clamped
        else:
            for item in _as_sequence(overrides_value):
                if isinstance(item, str):
                    normalized = item.strip()
                    if normalized:
                        permitted_marks.append(normalized)
    if permitted_marks or (sensitivity is not None):
        overrides_config: Dict[str, Any] = {}
        if permitted_marks:
            overrides_config["permitted_marks"] = permitted_marks
        if sensitivity is not None:
            overrides_config["sensitivity"] = sensitivity
        result["punctuation_overrides"] = overrides_config

    return result


def _normalize_diarization(value: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    bool_candidate = _coerce_bool(value)
    if bool_candidate is not None:
        result["diarization"] = bool_candidate
        return result

    mapping = _as_mapping(value)
    enabled = _coerce_bool(mapping.get("enabled"))
    if enabled is not None:
        result["diarization"] = enabled

    return result


def _normalize_max_final_latency(value: Any) -> float | None:
    latency_ms = _coerce_int(value)
    if latency_ms is None:
        return None
    clamped = _clamp_final_latency(latency_ms, _log)
    return round(clamped / 1000.0, 3)


def _clamp_final_latency(ms: int, log) -> int:
    lo, hi = _SM_MIN_FINAL_LATENCY_MS, _SM_MAX_FINAL_LATENCY_MS
    clamped = max(lo, min(ms, hi))
    if clamped != ms:
        log.info(
            "sm.max_delay.clamped wanted_ms=%s clamped_ms=%s lo_ms=%s hi_ms=%s",
            ms,
            clamped,
            lo,
            hi,
        )
    return clamped


def _normalize_custom_vocab(value: Any) -> list[Dict[str, Any]]:
    entries: list[Dict[str, Any]] = []

    def _add_entry(entry: Mapping[str, Any]) -> None:
        content = entry.get("content")
        if not isinstance(content, str):
            return
        normalized_content = content.strip()
        if not normalized_content:
            return

        normalized_entry: Dict[str, Any] = {"content": normalized_content}

        sounds_like_value = entry.get("sounds_like")
        sounds_like: list[str] = []
        for alt in _as_sequence(sounds_like_value):
            if isinstance(alt, str):
                normalized_alt = alt.strip()
                if normalized_alt:
                    sounds_like.append(normalized_alt)
        if sounds_like:
            normalized_entry["sounds_like"] = sounds_like

        boost_value = entry.get("boost")
        boost = _coerce_float(boost_value)
        if boost is not None:
            normalized_entry["boost"] = boost

        entries.append(normalized_entry)

    if isinstance(value, Mapping):
        entries_value = value.get("entries")
        if entries_value is None:
            _add_entry(value)
        else:
            for item in _as_sequence(entries_value):
                if isinstance(item, Mapping):
                    _add_entry(item)
                elif isinstance(item, str):
                    text = item.strip()
                    if text:
                        entries.append({"content": text})
        return entries

    for item in _as_sequence(value):
        if isinstance(item, Mapping):
            _add_entry(item)
        elif isinstance(item, str):
            text = item.strip()
            if text:
                entries.append({"content": text})

    return entries


def _merge_transcription_config(*configs: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for config in configs:
        for key, value in config.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)) and not value:
                continue
            if isinstance(value, dict) and not value:
                continue
            merged[key] = value
    return merged


def _speechmatics_policy_block(policy: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(policy, Mapping):
        return {}

    candidates: Iterable[Mapping[str, Any]] = (
        _as_mapping(policy.get("speechmatics")),
        _as_mapping(policy.get("asr")),
        _as_mapping(_as_mapping(policy.get("policy")).get("speechmatics")),
        _as_mapping(_as_mapping(_as_mapping(policy.get("policy")).get("asr")).get("speechmatics")),
    )

    for candidate in candidates:
        if candidate:
            return candidate
    return {}


def to_sm_params(policy: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Return Speechmatics realtime parameters derived from ``policy``.

    Args:
        policy: The application policy snapshot or vendor-specific override block.

    Returns:
        Dictionary ready to be sent as the Speechmatics ``StartRecognition`` payload.
    """

    policy_mapping = _as_mapping(policy)

    speechmatics_block = _speechmatics_policy_block(policy_mapping)
    asr_block = _as_mapping(policy_mapping.get("asr"))
    policy_block = _as_mapping(policy_mapping.get("policy"))
    if not asr_block:
        asr_block = _as_mapping(policy_block.get("asr"))

    language = _normalize_language(
        speechmatics_block.get("language"),
        asr_block.get("language"),
        _as_mapping(policy_block.get("nlu")).get("language"),
        policy_mapping.get("language"),
    ) or "en"

    enable_partials = _coerce_bool(
        speechmatics_block.get("enable_partials")
        if "enable_partials" in speechmatics_block
        else asr_block.get("enable_partials")
    )
    if enable_partials is None:
        enable_partials = True

    punctuation_config = _normalize_punctuation(speechmatics_block.get("punctuation"))
    diarization_config = _normalize_diarization(speechmatics_block.get("diarization"))

    max_delay = _normalize_max_final_latency(
        speechmatics_block.get("max_final_latency_ms")
    )

    profanity_filter = _coerce_bool(speechmatics_block.get("profanity_filter"))

    custom_vocab = _normalize_custom_vocab(speechmatics_block.get("custom_vocab"))

    transcription_config = _merge_transcription_config(
        {
            "language": language,
            "enable_partials": enable_partials,
            "operating_point": "standard",
        },
        punctuation_config,
        diarization_config,
        {"max_delay": max_delay},
        {"profanity_filter": profanity_filter},
        {"additional_vocab": custom_vocab},
    )

    # EXACT envelope per docs (Raw audio allows ONLY type/encoding/sample_rate)
    params: Dict[str, Any] = {
        "message": "StartRecognition",
        "audio_format": dict(_SM_AUDIO_FORMAT),
        "transcription_config": transcription_config,
    }

    return params
