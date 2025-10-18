"""Canonical catalog of flow trace event types and sections."""
from __future__ import annotations

from typing import Dict, Iterable, List

FlowEvent = Dict[str, object]
FlowSection = Dict[str, object]
FlowLevel = Dict[str, object]


def _event(type_: str, *, fields: Iterable[str] | None = None, notes: str | None = None) -> FlowEvent:
    payload: FlowEvent = {"type": type_}
    if fields:
        payload["fields"] = list(fields)
    if notes:
        payload["notes"] = notes
    return payload


FLOW_EVENT_CATALOG: List[FlowLevel] = [
    {
        "level": "flow",
        "label": "Flow",
        "description": "Top-level, readable timeline events",
        "sections": [
            {
                "name": "Session/Transport",
                "events": [
                    _event("session_open"),
                    _event("session_ready"),
                    _event("session_close"),
                ],
            },
            {
                "name": "Greet/Assistant",
                "events": [
                    _event("greet_start"),
                    _event("tts_queue"),
                    _event("tts_start"),
                    _event("tts_end"),
                    _event("assistant_end"),
                ],
            },
            {
                "name": "Turn lifecycle",
                "events": [
                    _event("confirm_open", fields=["phase", "turn_id"]),
                    _event("turn_commit", fields=["turn_id"]),
                    _event("confirm_close", fields=["reason"]),
                    _event("turn_abort", fields=["reason"]),
                ],
            },
            {
                "name": "ASR ready path",
                "events": [
                    _event("asr_connect"),
                    _event("asr_ready"),
                ],
            },
            {
                "name": "LLM",
                "events": [
                    _event("llm_start", fields=["model"]),
                    _event("llm_final", fields=["tokens_out", "chars"]),
                ],
            },
            {
                "name": "Wrap",
                "events": [
                    _event("transcript_saved"),
                ],
            },
        ],
    },
    {
        "level": "transition",
        "label": "Transition",
        "description": "Precise edges that belong under a parent flow event",
        "sections": [
            {
                "name": "Barge controller",
                "events": [
                    _event("barge_in", fields=["src", "tts_active"]),
                    _event("barge_resume"),
                ],
            },
            {
                "name": "Client VAD/PTT",
                "events": [
                    _event("ptt_down", fields=["during_tts"]),
                    _event("ptt_up"),
                    _event("vad_gate_open", fields=["reason", "rms"]),
                    _event("vad_gate_close", fields=["reason", "rms"]),
                ],
            },
            {
                "name": "ASR evidence",
                "events": [
                    _event("mic_first_chunk"),
                    _event("asr_partial_first", fields=["conf"]),
                    _event("evidence_gate_met", fields=["conf"]),
                    _event("asr_final", fields=["len", "conf", "turn_id"]),
                ],
            },
            {
                "name": "Transport",
                "events": [
                    _event("ws_error", fields=["code"], notes="Edge only"),
                ],
            },
        ],
    },
    {
        "level": "debug",
        "label": "Debug",
        "description": "Optional, high-value diagnostic events",
        "sections": [
            {
                "name": "Policy/Runtime",
                "events": [
                    _event("policy_snapshot"),
                    _event("runtime_flags"),
                ],
            },
            {
                "name": "Gating/Timers",
                "events": [
                    _event("gate_params"),
                    _event("gate_check", fields=["rule", "value", "threshold", "passed"]),
                    _event("timer_start", fields=["name", "ms"]),
                    _event("timer_cancel", fields=["name"]),
                    _event("timer_fire", fields=["name"]),
                ],
            },
            {
                "name": "Latencies",
                "events": [
                    _event("span_start", fields=["name", "attrs"]),
                    _event("span_end", fields=["name", "attrs"]),
                    _event("latency_tick", fields=["from", "to", "ms"]),
                ],
            },
            {
                "name": "Provider plumbing",
                "events": [
                    _event("asr_config"),
                    _event("asr_close", fields=["code", "reason"]),
                    _event("tts_event", fields=["op"]),
                    _event("asr_event", fields=["op"]),
                ],
            },
            {
                "name": "Queues/Drops",
                "events": [
                    _event("queue_depth", fields=["name", "depth", "watermark"]),
                    _event("frame_drop", fields=["path", "count"]),
                ],
            },
            {
                "name": "Audio stats",
                "events": [
                    _event(
                        "audio_stats",
                        fields=[
                            "rms_avg",
                            "rms_peak",
                            "clipped",
                            "silence_ratio",
                            "plc_frames",
                            "sample_rate",
                            "channels",
                            "mime",
                        ],
                    ),
                ],
            },
            {
                "name": "LLM meta",
                "events": [
                    _event("llm_meta", fields=["model", "cache_hit", "temp", "tools_enabled"]),
                    _event("llm_tools", fields=["calls", "failures"]),
                    _event("llm_safety", fields=["blocked", "categories"]),
                ],
            },
            {
                "name": "Network",
                "events": [
                    _event("ws_ping", fields=["rtt_ms", "jitter_ms"]),
                    _event("clock_skew", fields=["ms"]),
                ],
            },
            {
                "name": "Payload fingerprints",
                "events": [
                    _event("payload_sig", fields=["path", "bytes", "sha1_8"]),
                ],
            },
            {
                "name": "State snapshot",
                "events": [
                    _event(
                        "state_snapshot",
                        fields=[
                            "phase",
                            "tts_active",
                            "confirm_open",
                            "asr_ready",
                            "last_partial_age_ms",
                            "queue",
                        ],
                    ),
                ],
            },
            {
                "name": "Recovery",
                "events": [
                    _event("recover_ok", fields=["path"]),
                ],
            },
        ],
    },
    {
        "level": "raw",
        "label": "Raw",
        "description": "Lossless, batched data arrays",
        "sections": [
            {
                "name": "Raw streaming",
                "events": [
                    _event("vad_ticks", fields=["dt_ms", "rms", "gate"]),
                    _event("audio_chunks", fields=["dt_ms", "bytes", "mime", "containerized"]),
                    _event("dg_partials", fields=["dt_ms", "len", "conf", "final"]),
                    _event("dg_events", fields=["op"]),
                    _event("tts_chunks", fields=["dt_ms", "bytes", "mark"]),
                    _event("llm_stream", fields=["dt_ms", "bytes"]),
                    _event("ws_frames", fields=["dt_ms", "dir", "opcode", "len", "dropped"]),
                ],
            },
        ],
    },
]


def catalog_event_types() -> List[str]:
    types: List[str] = []
    for level in FLOW_EVENT_CATALOG:
        for section in level.get("sections", []):
            for event in section.get("events", []):
                type_name = str(event.get("type"))
                if type_name not in types:
                    types.append(type_name)
    return types


__all__ = ["FLOW_EVENT_CATALOG", "catalog_event_types"]
