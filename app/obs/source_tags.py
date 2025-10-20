"""Helpers for consistently tagging flow/admin events with sources and evidence."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Mapping, MutableMapping, Optional

FLOW_SCHEMA_VERSION = "v2-source-tags"
_MAX_EVIDENCE_BYTES = 400


def now_ms() -> int:
    """Return the current wall-clock time in milliseconds since the epoch."""

    return int(time.time() * 1000)


def ms_since(ts_ms: Optional[int]) -> Optional[int]:
    """Return the number of milliseconds elapsed since ``ts_ms``.

    ``None`` inputs or obviously invalid timestamps are ignored by returning ``None``.
    ``ts_ms`` is assumed to be milliseconds since the epoch.
    """

    if ts_ms is None:
        return None
    try:
        delta = now_ms() - int(ts_ms)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(delta, 0)


def resolve_barge_origin(
    manual_gate: bool,
    client_vad_recent_ms: Optional[int],
    asr_conf_recent: Optional[float],
    policy_triggered: bool,
) -> str:
    """Resolve the barge-in origin string for downstream tagging.

    Preference order is manual gate, server-side policy, client VAD activity, and
    finally ASR evidence. The output is always one of the supported enumerations.
    """

    if manual_gate:
        return "manual_ptt"
    if policy_triggered:
        return "server_policy"
    if client_vad_recent_ms is not None and client_vad_recent_ms >= 0:
        return "client_vad"
    if asr_conf_recent is not None:
        return "asr_evidence"
    return "client_vad"


def gate_snapshot(tts_active: bool, manual_gate: bool, vad_auto: bool) -> Dict[str, bool]:
    """Return a normalized snapshot of relevant gate state."""

    snapshot = {
        "tts_active": bool(tts_active),
        "manual_gate": bool(manual_gate),
        "vad_auto": bool(vad_auto),
    }
    return snapshot


def make_source_meta(
    source: str,
    *,
    gates: Optional[Mapping[str, Any]] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a schema/source metadata payload with optional gates/evidence."""

    meta: Dict[str, Any] = {"schema": FLOW_SCHEMA_VERSION, "source": source}
    if gates:
        try:
            meta["gates"] = {str(key): bool(value) for key, value in gates.items()}
        except Exception:
            # Best-effort normalization; fall back to the original mapping.
            meta["gates"] = dict(gates)  # type: ignore[arg-type]
    attach_evidence(meta, evidence)
    return meta


def _encode_size_bytes(payload: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        # Fall back to a conservative upper bound by stringifying the mapping.
        encoded = str(payload)
    return len(encoded.encode("utf-8", "ignore"))


def clamp_evidence(evidence: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a copy of ``evidence`` constrained to ``_MAX_EVIDENCE_BYTES``.

    Fields are added in a deterministic key order. Once the payload would exceed the
    byte budget, remaining fields are dropped. Invalid inputs fall back to an empty
    mapping to avoid raising.
    """

    if not evidence:
        return {}
    if not isinstance(evidence, Mapping):
        return {}

    trimmed: Dict[str, Any] = {}
    for key in sorted(evidence.keys()):
        value = evidence[key]
        candidate: Dict[str, Any] = dict(trimmed)
        candidate[key] = value
        if _encode_size_bytes(candidate) <= _MAX_EVIDENCE_BYTES:
            trimmed[key] = value
        else:
            continue
    return trimmed


def attach_evidence(target: MutableMapping[str, Any], evidence: Optional[Mapping[str, Any]]) -> None:
    """Attach evidence to ``target`` in-place while respecting size constraints.

    ``target`` is modified only when clamped evidence is non-empty. Errors during
    processing are swallowed to honour the safety requirement of never throwing.
    """

    if not isinstance(target, MutableMapping):
        return

    try:
        trimmed = clamp_evidence(evidence)
        if trimmed:
            target["evidence"] = trimmed
    except Exception:
        # Explicitly swallow unexpected errors to avoid breaking callers.
        return
