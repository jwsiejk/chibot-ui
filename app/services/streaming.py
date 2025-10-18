# app/services/streaming.py — Production-grade assistant framing
# Guarantees assistant_* frames are broadcast even if the LLM provider fails.
# Text-first design: generate/broadcast assistant text; TTS is optional elsewhere.

from __future__ import annotations
from typing import List, Dict, Tuple, Optional, Any, Iterable, Callable, Mapping
import base64
import hashlib
import threading, time as _t
import os
import re
import copy
import random

from ..security_state import get_profile, get_user
from ..session_state import (
    get as get_session_state,
    note_utterance_end,
    set_phase,
    should_emit_phase,
)
from .llm.flow_wrapper import make_llm_flow_tracker
from .tts.flow_wrapper import make_tts_flow_tracker
def _display_name_from_profile_or_email(meta: Optional[dict]) -> str:
    # 1) Profile name
    try:
        prof = get_profile() or {}
        name = str(prof.get("name") or "").strip()
        if name:
            return name.split()[0]
    except Exception:
        pass
    # 2) Email to first name
    email = None
    try:
        email = (meta or {}).get("email")
    except Exception:
        email = None
    if not email:
        try:
            email = get_user() or None
        except Exception:
            email = None
    if isinstance(email, str) and "@" in email:
        first = email.split("@",1)[0].split(".")[0].replace("_"," ").replace("-", " ").strip()
        if first:
            return first.title()
    return "there"

_GOAL_ASKS = [
    "what would you like to accomplish today?",
    "what's the goal for this session?",
    "what do you want to get done today?",
    "what should we tackle first?",
    "what outcome are you aiming for today?",
]

_CHIP_INTRO_RE = re.compile(r"^hey[\s,\-–—]*i['’`]?m\s+chip[.!\s]*$", re.IGNORECASE)

_TTS_COMPLETION_LOCK = threading.Lock()
_TTS_COMPLETION_EVENTS: Dict[str, Dict[str, threading.Event]] = {}
_TTS_LATEST_TURN: Dict[str, str] = {}

_FOUNDATION_FAILURE_LOCK = threading.Lock()
_FOUNDATION_DISABLED_UNTIL: float = 0.0
_FOUNDATION_LAST_ERROR: Optional[str] = None
_FOUNDATION_BACKOFF_SECONDS = 30.0


def _tts_event_key(turn_id: Optional[str]) -> str:
    return str(turn_id) if turn_id is not None else "greet"


def _prepare_tts_completion_event(session_id: str, turn_key: str) -> threading.Event:
    event = threading.Event()
    with _TTS_COMPLETION_LOCK:
        session_events = _TTS_COMPLETION_EVENTS.setdefault(session_id, {})
        session_events[turn_key] = event
        _TTS_LATEST_TURN[session_id] = turn_key
    return event


def _signal_tts_completion(session_id: str, turn_key: str) -> None:
    with _TTS_COMPLETION_LOCK:
        session_events = _TTS_COMPLETION_EVENTS.get(session_id)
        if not session_events:
            _TTS_LATEST_TURN.pop(session_id, None)
            return
        event = session_events.get(turn_key)
        if event:
            event.set()
        session_events.pop(turn_key, None)
        if not session_events:
            _TTS_COMPLETION_EVENTS.pop(session_id, None)
            _TTS_LATEST_TURN.pop(session_id, None)
        else:
            if _TTS_LATEST_TURN.get(session_id) == turn_key:
                remaining_keys = list(session_events.keys())
                if remaining_keys:
                    _TTS_LATEST_TURN[session_id] = remaining_keys[-1]
                else:
                    _TTS_LATEST_TURN.pop(session_id, None)


def wait_for_tts_completion(session_id: str,
                            turn_id: Optional[str] = None,
                            *,
                            timeout: Optional[float] = None) -> bool:
    with _TTS_COMPLETION_LOCK:
        session_events = _TTS_COMPLETION_EVENTS.get(session_id)
        if not session_events:
            event = None
        else:
            lookup_key: Optional[str]
            if turn_id:
                lookup_key = _tts_event_key(turn_id)
            else:
                lookup_key = _TTS_LATEST_TURN.get(session_id)
            event = session_events.get(lookup_key) if lookup_key else None
    if event is None:
        return True
    try:
        return bool(event.wait(timeout))
    except Exception:
        return False


def _clean_session_goal(goal: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a normalized session goal payload safe for meta/admin emission."""

    cleaned: Dict[str, Any] = {
        "phase": None,
        "depth": None,
        "delivery_pref": None,
        "working_intent": None,
        "entities": {"products": [], "keywords": []},
        "confirmed": [],
    }

    if not isinstance(goal, Mapping):
        return cleaned

    for key in ("phase", "depth", "delivery_pref", "working_intent"):
        if key in goal:
            cleaned[key] = goal.get(key)

    entities = goal.get("entities")
    if isinstance(entities, Mapping):
        for name in ("products", "keywords"):
            raw_items = entities.get(name)
            bucket: List[str] = []
            if isinstance(raw_items, Iterable) and not isinstance(raw_items, (str, bytes)):
                seen = set()
                for item in raw_items:
                    text = str(item or "").strip()
                    if not text:
                        continue
                    lowered = text.lower()
                    if lowered in seen:
                        continue
                    seen.add(lowered)
                    bucket.append(text)
            cleaned["entities"][name] = bucket

    confirmed = goal.get("confirmed")
    if isinstance(confirmed, Iterable) and not isinstance(confirmed, (str, bytes)):
        deduped: List[str] = []
        seen_conf = set()
        for item in confirmed:
            text = str(item or "").strip()
            if not text:
                continue
            if text in seen_conf:
                continue
            deduped.append(text)
            seen_conf.add(text)
        cleaned["confirmed"] = deduped
    elif isinstance(confirmed, str) and confirmed.strip():
        cleaned["confirmed"] = [confirmed.strip()]

    return cleaned


def is_listen_ready(session_id: str) -> bool:
    try:
        st = get_session_state(session_id)
    except Exception:
        return True
    try:
        recorder_active = bool(getattr(st, "recorder_active", False))
        asr_open = bool(getattr(st, "asr_stream_open", False))
    except Exception:
        return True
    return recorder_active or asr_open


def wait_for_listen_ready(session_id: str,
                          *,
                          timeout: float = 1.0,
                          poll_interval: float = 0.05) -> bool:
    if is_listen_ready(session_id):
        return True
    deadline = _t.time() + max(0.0, float(timeout))
    interval = max(0.01, float(poll_interval))
    while _t.time() < deadline:
        _t.sleep(interval)
        if is_listen_ready(session_id):
            return True
    return is_listen_ready(session_id)

def _pick_goal_ask(seed: str | None = None) -> str:
    try:
        h = int(hashlib.sha1((seed or 'seed').encode('utf-8')).hexdigest(), 16)
    except Exception:
        h = 0
    rng = random.Random(h)
    return _GOAL_ASKS[rng.randrange(len(_GOAL_ASKS))]

def _personalize_greet(base_line: str, meta: Optional[dict]) -> str:
    name = _display_name_from_profile_or_email(meta)
    ask = _pick_goal_ask(((meta or {}).get("session_id") or '') + '::' + ((meta or {}).get("turn_id") or ''))
    # Prefer a simple, human opener
    opener = f"Hey {name}, {ask}"
    if base_line and base_line.strip():
        lines = []
        for raw in (base_line or "").splitlines():
            stripped = (raw or "").strip()
            if not stripped:
                continue
            if _CHIP_INTRO_RE.match(stripped):
                continue
            lines.append(stripped)
        if lines:
            tail = "\n\n".join(lines)
            if tail.lower().strip().endswith("?"):
                return opener
            return opener + "\n\n" + tail
    return opener


from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble, format_kb_context
from .suggestions import hygienic_suggestions
from .vendor_clients import make_openai_client
from .nlg.humanize import humanize_text, sounds_botty
from .greet_idempotency import get_or_create_greet_turn, DEFAULT_TTL_SEC
from ..db import db
from ..ws.bus import bus
from ..policy import nudges
from ..obs import jlog as _jlog
from ..obs.nlu_logging import NluLoggingContext, create_context as _create_nlu_context
from ..personas.store import PersonaManager, PersonaStore
from ..personas.prompt_builder import build_messages
from .. import nlu as _nlu
from ..nlu.classifier import classify as classify_turn
from ..nlu.universal_interpreter import (
    ensure_all_fields as _ensure_universal_fields,
    interpret as _interpret_universal,
)
from ..dialog.policy import pick as pick_dialog_policy
from ..config import load_settings

try:
    from ..admin_log import emit as _admin_emit  # Admin log relay
except Exception:
    def _admin_emit(*a, **k):  # no-op if admin channel absent
        pass


def _broadcast_admin_ws_event(event: str, payload: Mapping[str, Any]) -> None:
    """Best-effort mirror of admin diagnostics onto the session WS bus."""
    try:
        sid = payload.get("session_id") or payload.get("sid")
    except Exception:
        sid = None
    if not sid:
        return
    try:
        sid_str = str(sid)
    except Exception:
        sid_str = sid  # type: ignore[assignment]

    frame = {"type": event}
    frame.update(dict(payload))
    frame.setdefault("session_id", sid_str)
    frame.setdefault("sid", sid_str)

    try:
        bus.broadcast(str(sid_str), frame)
    except Exception:
        pass


def _emit_turn_action_metadata(turn_id: Optional[str],
                               meta: Optional[Dict[str, Any]],
                               *,
                               is_greet: bool,
                               session_id: Optional[str] = None) -> None:
    """Emit structured NLU/policy metadata for admin observers."""
    if not turn_id or not isinstance(meta, dict):
        return

    if isinstance(meta.get(_DIALOG_NLU_KEY), dict):
        nlu_meta = meta.get(_DIALOG_NLU_KEY) or {}
    elif isinstance(meta.get("nlu"), dict):
        nlu_meta = meta.get("nlu") or {}
    else:
        nlu_meta = {}
    policy_payload = {
        "action": meta.get(_DIALOG_ACTION_KEY) or meta.get("action"),
        "verbosity": meta.get(_DIALOG_VERBOSITY_KEY) or meta.get("verbosity"),
        "show_suggestions": meta.get(_DIALOG_SHOW_SUGGESTIONS_KEY) or meta.get("show_suggestions"),
    }
    payload: Dict[str, Any] = {
        "turn_id": turn_id,
        "is_greet": bool(is_greet),
        "nlu": {
            "intent": nlu_meta.get("intent"),
            "topic": nlu_meta.get("topic"),
            "needs_scoping": nlu_meta.get("needs_scoping"),
            "wants_list": nlu_meta.get("wants_list"),
            "expected_depth": nlu_meta.get("expected_depth"),
            "confidence": nlu_meta.get("confidence"),
        },
        "policy": policy_payload,
    }
    session_id_value: Optional[str] = None
    if session_id:
        session_id_value = str(session_id)
    elif isinstance(meta, dict):
        sid_candidate = meta.get("session_id") or meta.get("sid")
        if sid_candidate:
            try:
                session_id_value = str(sid_candidate)
            except Exception:
                session_id_value = sid_candidate  # type: ignore[assignment]
    if session_id_value:
        payload["session_id"] = session_id_value
    try:
        _admin_emit("turn_action_metadata", **payload)
    except Exception:
        pass
    _broadcast_admin_ws_event("turn_action_metadata", payload)


def _get_persona_for_session(session_id: str) -> Dict:
    try:
        sess = db.memory.get('sessions', {}).get(session_id) or {}
        persona_id = (sess.get('persona_id') or 'chip')
        return db.memory.get('personas', {}).get(persona_id) or {'id': 'chip'}
    except Exception:
        return {'id': 'chip'}


def _build_prompt(seed_text: str, persona: Dict, kb_snippets: List[str], teacher_move: Optional[str]) -> str:
    parts = [build_persona_preamble(persona)]
    if kb_snippets:
        parts.append(format_kb_context(kb_snippets))
    if teacher_move:
        parts.append(f"Teacher move: {teacher_move}.")
    parts.append(f"User said: {seed_text.strip()}")
    return "\n\n".join(parts)


# --- text hygiene -------------------------------------------------------------
# Remove ANY occurrence (not just trailing) of legacy "[KB:n]" stamps, case-insensitive.
_KB_TAG_RE = re.compile(r"\s*\[(?:KB|kb)\s*:\s*\d+\]\s*")

def _scrub_debug_stamps(s: str) -> str:
    """
    Remove legacy debug stamps like "[KB:0]" that were historically appended to
    assistant text for telemetry. We keep the value as metadata instead.
    This removes ALL occurrences safely without touching other content.
    """
    if not s:
        return s
    # Replace any matches with a single space, then trim.
    return _KB_TAG_RE.sub(" ", s).strip()



_LIST_PREFIX_RE = re.compile(r'^[\s\-\*•\d]+[\.)\-\s]+')
def _strip_list_prefix(line: str) -> str:
    line = (line or "").strip()
    if not line:
        return line
    return _LIST_PREFIX_RE.sub('', line).strip()

def _short_greeting(text: str) -> str:
    try:
        raw = _collapse_whitespace(text or "Hi there!")
        if not raw:
            return "Hi there!"
        # If the first non-empty line looks like a list item (e.g., "1. ..." or "• ..."),
        # prefer its content as the greet text.
        first_line = None
        for ln in (text or "").splitlines():
            ln = (ln or "").strip()
            if ln:
                first_line = ln
                break
        if first_line:
            stripped = _strip_list_prefix(first_line)
            if stripped and stripped != first_line:
                candidate = stripped
            else:
                candidate = raw
        else:
            candidate = raw
        limited = _limit_sentences(candidate, 1)
        snippet = limited or candidate
        shortened = _truncate_chars(snippet, 160)
        result = (shortened or snippet or "").strip()
        return result or "Hi there!"
    except Exception:
        fallback = (text or "").strip()
        return fallback or "Hi there!"


SETTINGS = load_settings()
ENABLE_CHIP_FOUNDATION = SETTINGS.enable_chip_foundation
ENABLE_POLICY_CHIPS = SETTINGS.enable_policy_chips
SUGGESTION_MAX = SETTINGS.suggestion_max
STRICT_SYSTEM_SHIM = (
    "Sound like a human Pure Storage expert. Keep openings short, prefer tight bullets when explaining steps, "
    "and never mention being an AI or chatbot."
)

_WS_SOURCES = {"ws_greet", "user_ws"}
_WS_PIPELINE_UNAVAILABLE_NOTE = "ws_pipeline_unavailable"
_WS_PIPELINE_MESSAGE = "I'm still warming up. Please try again in a moment."
_LEGACY_WARMUP_LINE = "Hi! I’m ready to help. (Model is warming up.)"


_DIALOG_ACTION_KEY = "dialog_action"
_DIALOG_VERBOSITY_KEY = "dialog_verbosity"
_DIALOG_SHOW_SUGGESTIONS_KEY = "dialog_show_suggestions"
_DIALOG_NLU_KEY = "dialog_nlu"
_DIALOG_POLICY_KEY = "dialog_policy"


_ACTION_ALIASES = {
    "ask_clarify": "clarify",
    "check_understanding": "clarify",
    "give_brief_answer": "high_level",
    "respond": "high_level",
    "answer": "high_level",
}


def _normalize_move_name(move: Optional[str]) -> str:
    try:
        return str(move or "").strip().lower()
    except Exception:
        return ""


_TEACHER_MOVE_FAMILY = {
    "ask_clarify": "clarify",
    "clarify": "clarify",
    "check_understanding": "clarify",
    "offer_steps": "answer",
    "respond": "answer",
    "compare": "compare",
    "visualize": "answer",
    "deep_dive": "deep_dive",
    "summarize_next_actions": "summarize",
    "summarize": "summarize",
    "high_level": "answer",
}


def _teacher_move_family(move: Optional[str]) -> str:
    normalized = _normalize_move_name(move)
    return _TEACHER_MOVE_FAMILY.get(normalized, "answer")


def _summarize_used_docs(docs: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    if not docs:
        return []
    summary: List[Dict[str, Any]] = []
    for idx, item in enumerate(docs):
        try:
            if isinstance(item, (bytes, bytearray)):
                raw_bytes = bytes(item)
            else:
                raw_bytes = str(item).encode("utf-8", "ignore")
        except Exception:
            raw_bytes = repr(item).encode("utf-8", "ignore")
        digest = hashlib.sha256(raw_bytes).hexdigest()[:8]
        entry: Dict[str, Any] = {"idx": idx, "hash": digest, "len": len(raw_bytes)}
        if isinstance(item, dict):
            for key in ("id", "doc_id", "source", "path", "title"):
                value = item.get(key)
                if value:
                    try:
                        entry["tag"] = str(value)[:40]
                    except Exception:
                        entry["tag"] = repr(value)[:40]
                    break
        summary.append(entry)
    return summary


def _log_policy_decision(*,
                         session_id: str,
                         turn_id: str,
                         resolved_move: Optional[str],
                         normalized_action: Optional[str],
                         policy_move: Optional[str],
                         teacher_move_seed: Optional[str],
                         fallback_fired: bool,
                         fallback_reason: Optional[str],
                         meta: Optional[Dict[str, Any]],
                         nlu_meta: Optional[Dict[str, Any]],
                         used_docs_source: Optional[Iterable[Any]]) -> None:
    try:
        normalized_action_name = _normalize_move_name(normalized_action)
        policy_move_name = _normalize_move_name(policy_move)
        seed_move_name = _normalize_move_name(teacher_move_seed)
        resolved_move_name = _normalize_move_name(resolved_move)

        reason = "policy"
        if fallback_fired:
            reason = "fallback"
        else:
            hint = None
            if isinstance(meta, dict):
                hint_val = meta.get("policy_override_reason")
                if isinstance(hint_val, str) and hint_val.strip():
                    hint = hint_val.strip().lower()
            if hint:
                if "fallback" in hint:
                    reason = "fallback"
                elif "override" in hint:
                    reason = "meta_override"
                elif hint in {"policy", "meta_override", "fallback"}:
                    reason = hint
            elif normalized_action_name:
                if policy_move_name and normalized_action_name != policy_move_name:
                    reason = "meta_override"
                elif not policy_move_name:
                    reason = "meta_override"

        nlu_intent = None
        nlu_confidence = None
        if isinstance(nlu_meta, dict):
            nlu_intent = nlu_meta.get("intent")
            confidence_raw = nlu_meta.get("confidence")
            if confidence_raw is not None:
                if isinstance(confidence_raw, (int, float)):
                    nlu_confidence = float(confidence_raw)
                else:
                    try:
                        nlu_confidence = float(str(confidence_raw))
                    except Exception:
                        nlu_confidence = None

        used_docs = _summarize_used_docs(used_docs_source)

        payload: Dict[str, Any] = {
            "sid": session_id,
            "turn_id": turn_id,
            "teacher_move": resolved_move_name or None,
            "teacher_move_family": _teacher_move_family(resolved_move_name),
            "reason": reason,
            "fallback_fired": bool(fallback_fired),
            "fallback_reason": fallback_reason,
            "nlu_intent": nlu_intent,
            "nlu_confidence": nlu_confidence,
        }
        if session_id:
            try:
                payload["session_id"] = str(session_id)
            except Exception:
                payload["session_id"] = session_id  # type: ignore[assignment]
        if normalized_action_name:
            payload["meta_action"] = normalized_action_name
        if policy_move_name:
            payload["policy_move"] = policy_move_name
        if seed_move_name and seed_move_name != resolved_move_name:
            payload["persona_move"] = seed_move_name
        if used_docs:
            payload["used_docs"] = used_docs
        _jlog("policy.decision", **payload)

        if callable(_admin_emit):
            label_move = (
                resolved_move_name
                or policy_move_name
                or normalized_action_name
                or seed_move_name
                or "unknown"
            )
            try:
                _admin_emit(
                    "policy_decision",
                    event="policy_decision",
                    label=f"policy_decision: {label_move}",
                    decision=label_move,
                    **payload,
                )
            except Exception:
                pass
        _broadcast_admin_ws_event("policy_decision", payload)
    except Exception:
        pass


def _should_use_foundation(seed_text: str) -> bool:
    if not ENABLE_CHIP_FOUNDATION:
        return False
    if _foundation_backoff_active():
        return False
    try:
        normalized = str(seed_text or "").strip().lower()
    except Exception:
        return False
    if not normalized:
        return False
    return True


_POLICY_SUGGESTION_ACTIONS = frozenset({"clarify", "ask_clarify", "offer_steps"})
_WELCOME_MOVE = "offer_steps"
_WELCOME_INTENTS = {"greet", "idle"}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _get_dialog_action(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    action = meta.get(_DIALOG_ACTION_KEY)
    if action:
        return action
    return meta.get("action")


def _get_dialog_show_suggestions(meta: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(meta, dict):
        return False
    if _DIALOG_SHOW_SUGGESTIONS_KEY in meta:
        return bool(meta.get(_DIALOG_SHOW_SUGGESTIONS_KEY))
    return bool(meta.get("show_suggestions"))


def _policy_action(policy: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(policy, dict):
        return None
    action = policy.get("action") or policy.get("teacher_move")
    return action if isinstance(action, str) else None


def _extract_intent_from_meta(meta: Optional[Dict[str, Any]],
                              fallback: Optional[str] = None) -> Optional[str]:
    """Best-effort intent extraction from dialog metadata."""
    intent: Optional[str] = None
    source = meta if isinstance(meta, dict) else None
    if source:
        for key in (_DIALOG_NLU_KEY, "nlu"):
            block = source.get(key)
            if isinstance(block, dict):
                raw = block.get("intent")
                if raw is not None:
                    try:
                        intent = str(raw or "").strip().lower()
                    except Exception:
                        intent = None
                    else:
                        if intent:
                            return intent
        # Some callers may store intent directly on the meta payload
        raw_meta_intent = source.get("intent")
        if raw_meta_intent is not None:
            try:
                intent = str(raw_meta_intent or "").strip().lower()
            except Exception:
                intent = None
            else:
                if intent:
                    return intent
    if fallback is not None:
        try:
            intent = str(fallback or "").strip().lower()
        except Exception:
            intent = None
    return intent if intent else None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if isinstance(value, str):
            txt = value.strip()
            if not txt:
                return None
            return int(float(txt))
        return int(value)
    except Exception:
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return None
    try:
        return bool(value)
    except Exception:
        return None


def _history_has_user_turn(history: Any) -> Optional[bool]:
    if history is None:
        return None
    entries: Iterable[Any]
    if isinstance(history, dict):
        entries = history.values()
    elif isinstance(history, (list, tuple, set)):
        entries = history
    else:
        return None

    saw_entry = False
    for item in entries:
        saw_entry = True
        role: Optional[str] = None
        if isinstance(item, dict):
            role = item.get("role") or item.get("speaker")
        elif isinstance(item, (list, tuple)) and item:
            role = item[0]
        elif isinstance(item, str):
            role = item.split(":", 1)[0]
        if isinstance(role, str) and role.strip().lower() == "user":
            return True
    if not saw_entry:
        return False
    return False


def _has_prior_user_turn(meta: Optional[Dict[str, Any]],
                         *,
                         default_intent: Optional[str] = None) -> bool:
    """Infer whether the conversation has previously seen a user turn."""
    source = meta if isinstance(meta, dict) else {}

    for key in ("has_user_turns", "has_prior_user_turn", "has_prior_user_turns"):
        if key in source:
            coerced = _coerce_bool(source.get(key))
            if coerced is not None:
                return bool(coerced)

    for key in ("prior_user_turns", "user_turn_count", "turn_index", "user_turn_index"):
        if key in source:
            count = _coerce_int(source.get(key))
            if count is not None:
                return count > 0

    history = (
        source.get("dialog_history")
        or source.get("history")
        or source.get("messages")
    )
    history_result = _history_has_user_turn(history)
    if history_result is not None:
        return bool(history_result)

    last_user = source.get("last_user_turn_id")
    if isinstance(last_user, str) and last_user.strip():
        return True

    intent = _extract_intent_from_meta(source, fallback=default_intent)
    if intent in _WELCOME_INTENTS:
        return False

    # When we cannot confidently infer history, assume prior turns exist to
    # avoid overriding established conversations.
    return True


def _should_force_welcome_move(meta: Optional[Dict[str, Any]],
                               *,
                               is_greet: bool,
                               intent: Optional[str] = None) -> bool:
    if is_greet:
        return True
    return not _has_prior_user_turn(meta, default_intent=intent)


def _resolve_show_suggestions(meta: Dict[str, Any],
                              policy: Optional[Dict[str, Any]],
                              cfg: Dict[str, Any]) -> bool:
    suggestions_enabled = bool(cfg.get("suggestions_enabled", True))
    if not suggestions_enabled:
        return False

    def _coerce_pref(raw: Any) -> Optional[bool]:
        if raw is None:
            return None
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return None
        try:
            return bool(raw)
        except Exception:
            return None

    policy_flag: Optional[bool] = None
    if isinstance(policy, dict) and "show_suggestions" in policy:
        policy_flag = _coerce_pref(policy.get("show_suggestions"))
    elif isinstance(meta, dict):
        if _DIALOG_SHOW_SUGGESTIONS_KEY in meta:
            policy_flag = _coerce_pref(meta.get(_DIALOG_SHOW_SUGGESTIONS_KEY))
        elif "show_suggestions" in meta:
            policy_flag = _coerce_pref(meta.get("show_suggestions"))

    if policy_flag is None:
        action = _get_dialog_action(meta) if isinstance(meta, dict) else None
        if not action:
            action = _policy_action(policy)
        if action:
            policy_flag = action in _POLICY_SUGGESTION_ACTIONS

    if policy_flag is None:
        return False
    return bool(policy_flag) and suggestions_enabled


def _apply_system_shim(messages: List[Dict[str, str]], shim: str) -> List[Dict[str, str]]:
    cloned: List[Dict[str, str]] = [dict(m) for m in messages]
    for msg in cloned:
        if msg.get("role") == "system":
            msg["content"] = (msg.get("content") or "").rstrip() + "\n" + shim
            return cloned
    cloned.insert(0, {"role": "system", "content": shim})
    return cloned


def _normalize_action(meta: Optional[Dict[str, Any]], default: str = "respond") -> str:
    action = _get_dialog_action(meta) or default
    try:
        normalized = str(action or "").strip().lower()
    except Exception:
        normalized = ""
    mapped = _ACTION_ALIASES.get(normalized, normalized)
    if not mapped:
        mapped = _ACTION_ALIASES.get(default, default)
    return mapped or default


def _extract_session_id(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    for key in ("session_id", "sid", "session", "conversation_id"):
        value = meta.get(key)
        if value is None:
            continue
        try:
            candidate = str(value or "").strip()
        except Exception:
            candidate = ""
        if candidate:
            return candidate
    return None


def _dialog_verbosity(meta: Optional[Dict[str, Any]]) -> str:
    if not isinstance(meta, dict):
        return "normal"
    raw = meta.get("verbosity")
    if raw is None:
        raw = meta.get(_DIALOG_VERBOSITY_KEY)
    try:
        lowered = str(raw or "").strip().lower()
    except Exception:
        lowered = ""
    if lowered in {"brief", "normal"}:
        return lowered
    if lowered == "medium":
        return "normal"
    return "normal"


def _extract_topic(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    topic: Optional[str] = None
    nlu_meta = meta.get("nlu")
    if isinstance(nlu_meta, dict):
        topic = nlu_meta.get("topic")
    if not topic:
        dialog_nlu = meta.get(_DIALOG_NLU_KEY)
        if isinstance(dialog_nlu, dict):
            topic = dialog_nlu.get("topic")
    if not topic:
        return None
    try:
        cleaned = str(topic or "").strip()
    except Exception:
        cleaned = ""
    cleaned = cleaned.rstrip("?.! ")
    return cleaned or None


def _build_clarify_question(seed_text: str, meta: Optional[Dict[str, Any]]) -> str:
    topic = _extract_topic(meta)
    verbosity = _dialog_verbosity(meta)
    text: str
    if topic:
        if verbosity == "brief":
            text = f"What part of {topic} should we focus on?"
        else:
            text = f"What part of {topic} should we focus on so I can help?"
    else:
        if verbosity == "brief":
            text = "What should we clarify so I can help?"
        else:
            text = "What detail should we clarify so I can point you the right way?"
    return humanize_text(text)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _limit_sentences(text: str, max_sentences: int) -> str:
    collapsed = _collapse_whitespace(text)
    if not collapsed:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(collapsed)
    if not parts:
        return collapsed
    limited: List[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        limited.append(candidate)
        if len(limited) >= max_sentences:
            break
    if not limited:
        return collapsed
    return " ".join(limited)


def _truncate_chars(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    shortened = text[:limit].rstrip(" ,;:-")
    if not shortened:
        return text[:limit]
    if shortened.endswith(('.', '!', '?')):
        return shortened
    return shortened + "…"


def _extract_list_items(text: str) -> List[str]:
    if not text:
        return []
    items: List[str] = []
    for raw_line in text.splitlines():
        cleaned = raw_line.strip()
        if not cleaned:
            continue
        cleaned = re.sub(r"^[\-\*•\d]+[\.)\s]*", "", cleaned).strip()
        if cleaned:
            items.append(_collapse_whitespace(cleaned))
    if items:
        return items
    collapsed = _collapse_whitespace(text)
    if not collapsed:
        return []
    parts = _SENTENCE_SPLIT_RE.split(collapsed)
    return [part.strip(" -") for part in parts if part.strip(" -")]


def _format_offer_steps(text: str, meta: Optional[Dict[str, Any]]) -> str:
    verbosity = _dialog_verbosity(meta)
    max_items = 3 if verbosity == "brief" else 4
    max_chars = 80 if verbosity == "brief" else 110
    items = _extract_list_items(text)
    if not items:
        return ""
    formatted: List[str] = []
    for idx, item in enumerate(items[:max_items], start=1):
        cleaned = _truncate_chars(item, max_chars)
        formatted.append(f"{idx}. {cleaned}")
    return "\n".join(formatted)


def _format_brief_answer(text: str, meta: Optional[Dict[str, Any]]) -> str:
    verbosity = _dialog_verbosity(meta)
    max_sentences = 1 if verbosity == "brief" else 2
    max_chars = 160 if verbosity == "brief" else 220
    limited = _limit_sentences(text, max_sentences)
    return _truncate_chars(limited, max_chars)


def _format_next_actions(text: str, meta: Optional[Dict[str, Any]]) -> str:
    verbosity = _dialog_verbosity(meta)
    max_items = 2 if verbosity == "brief" else 3
    max_chars = 90 if verbosity == "brief" else 120
    items = _extract_list_items(text)
    if not items:
        return ""
    trimmed: List[str] = []
    for item in items[:max_items]:
        cleaned = _truncate_chars(item, max_chars)
        trimmed.append(f"• {cleaned}")
    if verbosity == "brief":
        return "\n".join(trimmed)
    return "Next actions:\n" + "\n".join(trimmed)


def _apply_action_shape(action: str,
                        text: str,
                        meta: Optional[Dict[str, Any]]) -> str:
    if not text:
        return text
    normalized = (action or "").strip().lower()
    if normalized in {"give_brief_answer", "high_level", "clarify"}:
        return _format_brief_answer(text, meta)
    if normalized == "offer_steps":
        return _format_offer_steps(text, meta)
    if normalized == "summarize_next_actions":
        return _format_next_actions(text, meta)
    return text


def _resolve_foundation_model(cfg: Mapping[str, Any] | Dict[str, Any]) -> str:
    """Derive the foundation model name from config or environment."""

    candidate: Optional[str] = None
    if isinstance(cfg, Mapping):
        for key in ("openai_model", "OPENAI_MODEL"):
            try:
                value = cfg.get(key)  # type: ignore[arg-type]
            except Exception:
                value = None
            if value:
                candidate = str(value)
                break
    if not candidate:
        env_model = os.getenv("OPENAI_MODEL")
        if env_model:
            candidate = env_model
    return str(candidate or "gpt-4o-mini")


def _resolve_provider_model(provider: Any) -> Optional[str]:
    """Best effort extraction of a model identifier from a provider."""

    if provider is None:
        return None
    try:
        model_attr = getattr(provider, "model")
    except Exception:
        model_attr = None
    if callable(model_attr):
        try:
            model_attr = model_attr()
        except Exception:
            model_attr = None
    if model_attr:
        return str(model_attr)
    try:
        fallback = getattr(provider, "MODEL")
    except Exception:
        fallback = None
    if fallback:
        try:
            return str(fallback)
        except Exception:
            return None
    return None


def _call_foundation_llm(messages: List[Dict[str, str]],
                         cfg: Dict[str, Any],
                         telemetry: Optional[NluLoggingContext] = None) -> Tuple[str, Dict[str, Any]]:
    try:
        client = make_openai_client()
    except Exception as exc:
        _note_foundation_failure(exc)
        raise
    model = _resolve_foundation_model(cfg)
    temperature = float(cfg.get("gen_temperature", 0.3))
    top_p = float(cfg.get("gen_top_p", 1.0))
    payload = [dict(m) for m in messages]
    prompt_tokens_estimate = 0
    for msg in payload:
        content = msg.get("content")
        if isinstance(content, str):
            prompt_tokens_estimate += len(content.split())
    cache_status = str(cfg.get("foundation_cache_status") or "unknown")
    tool_allowlist = cfg.get("tool_allowlist")
    allowlist_payload: Optional[Iterable[str]] = None
    if isinstance(tool_allowlist, (list, tuple, set)):
        allowlist_payload = tool_allowlist
    elif tool_allowlist is not None:
        allowlist_payload = [str(tool_allowlist)]
    if telemetry:
        telemetry.log_llm_request(
            model=model,
            temperature=temperature,
            top_p=top_p,
            prompt_tokens=prompt_tokens_estimate,
            cache_status=cache_status,
            tool_allowlist=allowlist_payload,
        )
    resp = client.chat.completions.create(
        model=model,
        messages=payload,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )
    content = ""
    try:
        content = (resp.choices[0].message.content or "").strip()
    except Exception:
        content = ""
    usage = getattr(resp, "usage", None)
    prompt_tokens = None
    completion_tokens = None
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        if prompt_tokens is None and isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
    finish_reason = None
    try:
        finish_reason = resp.choices[0].finish_reason
    except Exception:
        finish_reason = None
    preview = None
    if content:
        preview = " ".join(content.split()[:8])
    info = {
        "output_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "finish_reason": finish_reason,
        "preview": preview,
    }
    return content, info


def _call_foundation_with_retry(messages: List[Dict[str, str]],
                                cfg: Dict[str, Any],
                                telemetry: Optional[NluLoggingContext] = None) -> Tuple[str, Dict[str, Any]]:
    primary, primary_info = _call_foundation_llm(messages, cfg, telemetry=telemetry)
    human = humanize_text(primary)
    info = dict(primary_info)
    if human:
        info.setdefault("preview", " ".join(human.split()[:8]))
    if not human:
        return human, info
    if not sounds_botty(human):
        return human, info
    try:
        strict_msgs = _apply_system_shim(messages, STRICT_SYSTEM_SHIM)
        strict_reply, strict_info = _call_foundation_llm(strict_msgs, cfg, telemetry=telemetry)
        strict_human = humanize_text(strict_reply)
        if strict_human:
            strict_info.setdefault("preview", " ".join(strict_human.split()[:8]))
            return strict_human, strict_info
    except Exception:
        pass
    return human, info


def _broadcast_frames(session_id: str,
                      frames: List[Dict],
                      turn_id: Optional[str] = None) -> None:
    try:
        for fr in frames:
            bus.broadcast(session_id, fr)
            if not isinstance(fr, dict):
                continue
            ftype = fr.get('type')
            admin_turn_id = fr.get('turn_id')
            if admin_turn_id is None:
                admin_turn_id = turn_id
            if ftype == 'assistant_chunk':
                if admin_turn_id is not None:
                    _admin_emit('assistant_chunk', session_id=session_id, turn_id=str(admin_turn_id))
                else:
                    _admin_emit('assistant_chunk', session_id=session_id)
            elif ftype == 'assistant_end':
                if admin_turn_id is not None:
                    _admin_emit('assistant_end', session_id=session_id, turn_id=str(admin_turn_id))
                else:
                    _admin_emit('assistant_end', session_id=session_id)
    except Exception:
        pass


def _suggestion_cap(default: int = 4) -> int:
    cap = SUGGESTION_MAX if SUGGESTION_MAX is not None else default
    try:
        cap_int = int(cap)
    except Exception:
        return default
    return max(0, cap_int)


def _normalize_suggestion_item(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("text", "label", "title"):
            val = raw.get(key)
            if isinstance(val, str):
                return val.strip()
        for val in raw.values():
            if isinstance(val, str):
                return val.strip()
    try:
        return str(raw or "").strip()
    except Exception:
        return ""


def merge_suggestions(*lists: Iterable[Any], cap: int = 4) -> List[str]:
    limit = _suggestion_cap()
    if cap is not None:
        limit = min(limit, cap)
    if limit <= 0:
        return []

    seen: List[str] = []
    seen_set = set()
    for lst in lists:
        if not lst:
            continue
        for raw in lst:
            text = _normalize_suggestion_item(raw)
            if not text:
                continue
            if len(text) > 50:
                text = text[:50].rstrip()
            if not text or text in seen_set:
                continue
            seen.append(text)
            seen_set.add(text)
            if len(seen) >= limit:
                return seen
    return seen[:limit]


def build_suggestion_items(items: List[str]) -> List[Dict[str, str]]:
    return [{"text": it} for it in items]


def _log_suggestions_made(turn_id: Optional[str],
                          policy_chips: Optional[Iterable[Any]],
                          legacy_suggestions: Optional[Iterable[Any]],
                          merged_suggestions: Optional[Iterable[Any]],
                          cfg: Optional[Dict[str, Any]],
                          *,
                          session_id: Optional[str] = None) -> None:
    try:
        cfg_map: Dict[str, Any]
        if isinstance(cfg, dict):
            cfg_map = cfg
        else:
            cfg_map = {}

        def _coerce_limit(value: Any, default: int) -> int:
            try:
                if value is None:
                    return default
                coerced = int(value)
                if coerced < 0:
                    return default
                return coerced
            except Exception:
                return default

        max_items = _coerce_limit(cfg_map.get("suggestions_max_items"), _suggestion_cap())
        max_words = _coerce_limit(cfg_map.get("suggestions_max_words"), 7)

        def _display_text(raw: Any) -> str:
            text = _normalize_suggestion_item(raw)
            if len(text) > 50:
                text = text[:50].rstrip()
            return text

        policy_set = {
            text
            for text in (_display_text(item) for item in (policy_chips or []))
            if text
        }
        retrieval_set = {
            text
            for text in (_display_text(item) for item in (legacy_suggestions or []))
            if text
        }

        items: List[Dict[str, str]] = []
        for raw in merged_suggestions or []:
            text = _display_text(raw)
            if not text:
                continue
            if text in policy_set:
                source = "policy"
            elif text in retrieval_set:
                source = "retrieval"
            else:
                source = "retrieval"
            items.append({"text": text, "source": source})

        sid_value: Optional[str] = None
        if session_id:
            try:
                sid_value = str(session_id)
            except Exception:
                sid_value = session_id  # type: ignore[assignment]

        log_payload: Dict[str, Any] = {
            "turn_id": str(turn_id) if turn_id is not None else None,
            "items": items,
            "max_items": max_items,
            "max_words_per_item": max_words,
            "count": len(items),
        }
        if sid_value:
            log_payload["sid"] = sid_value
        _jlog("suggestions_made", **log_payload)

        admin_payload = dict(log_payload)
        admin_payload["event"] = "suggestions_made"
        if sid_value:
            admin_payload.setdefault("session_id", sid_value)
        try:
            _admin_emit("suggestions_made", **admin_payload)
        except Exception:
            pass
        _broadcast_admin_ws_event("suggestions_made", admin_payload)
    except Exception:
        pass


def classify(seed_text: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run lightweight classifiers and awareness annotators for a user turn."""
    base_meta = meta or {}
    labels: Dict[str, Any] = {}

    try:
        primary_labels = classify_turn(seed_text, base_meta)
        if isinstance(primary_labels, dict):
            labels.update(primary_labels)
    except Exception:
        pass

    try:
        awareness = annotate(seed_text, base_meta)
        if isinstance(awareness, dict):
            labels.update({k: v for k, v in awareness.items() if k not in labels})
            # Allow awareness annotators to augment existing labels without
            # clobbering core classifier outputs like intent/topic.
            for key, value in awareness.items():
                if key not in {"intent", "topic", "expected_depth", "needs_scoping"}:
                    labels[key] = value
    except Exception:
        pass

    try:
        engagement = score_engagement(seed_text, base_meta)
        if isinstance(engagement, dict):
            for key, value in engagement.items():
                if key not in labels:
                    labels[key] = value
    except Exception:
        pass

    if "intent" not in labels:
        text = (seed_text or "").strip().lower()
        if not text:
            labels["intent"] = "idle"
        elif text == "greet":
            labels["intent"] = "greet"
        elif seed_text.endswith("?") if isinstance(seed_text, str) else False:
            labels["intent"] = "broad_topic_help"
        else:
            labels["intent"] = "broad_topic_help"

    if "confidence" not in labels:
        labels["confidence"] = 0.0 if not (seed_text or "").strip() else 0.5

    return labels


def _planner_feature_summary(
    dialog_nlu: Optional[Mapping[str, Any]], universal: Optional[Mapping[str, Any]]
) -> List[str]:
    candidates: List[Tuple[str, float]] = []

    def _add(label: Optional[str], weight: float) -> None:
        if not label:
            return
        candidates.append((str(label), weight))

    nlu = dialog_nlu or {}
    try:
        emit_nlu_for_e2e(nlu, session_goal)
    except Exception:
        pass
    uni = universal or {}

    intent = nlu.get("intent") if isinstance(nlu, Mapping) else None
    if intent:
        _add(f"intent:{intent}", 1.0)

    topic = nlu.get("topic") if isinstance(nlu, Mapping) else None
    if topic:
        _add(f"topic:{topic}", 0.75)

    if isinstance(uni, Mapping) and uni.get("needs_clarification"):
        _add("needs_clarification", 0.9)

    missing: Iterable[Any] = []
    if isinstance(uni, Mapping):
        raw_missing = uni.get("missing")
        if isinstance(raw_missing, (list, tuple, set)):
            missing = raw_missing
        elif raw_missing:
            missing = [raw_missing]
    for idx, item in enumerate(missing):
        text = str(item).strip()
        if not text:
            continue
        _add(f"missing:{text}", max(0.5, 0.85 - idx * 0.05))

    if isinstance(uni, Mapping) and uni.get("multi_intent"):
        _add("multi_intent", 0.6)

    if isinstance(uni, Mapping) and uni.get("out_of_domain"):
        _add("out_of_domain", 0.6)

    if isinstance(uni, Mapping):
        depth = str(uni.get("depth") or "").strip()
        if depth and depth not in {"normal"}:
            _add(f"depth:{depth}", 0.5)

        delivery = str(uni.get("delivery_pref") or "").strip()
        if delivery and delivery not in {"explain"}:
            _add(f"delivery:{delivery}", 0.5)

        urgency = str(uni.get("urgency") or "").strip()
        if urgency and urgency not in {"normal"}:
            _add(f"urgency:{urgency}", 0.45)

    if not candidates:
        return []

    sorted_features = sorted(candidates, key=lambda item: item[1], reverse=True)
    return [label for label, _ in sorted_features[:5]]


def prepare_turn_metadata(seed_text: str,
                          meta: Optional[Dict[str, Any]] = None,
                          *,
                          cfg: Optional[Dict[str, Any]] = None,
                          telemetry: Optional[NluLoggingContext] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Ensure dialog metadata is populated for downstream consumers."""
    cfg = cfg or db.get_config()
    incoming_meta = meta if isinstance(meta, dict) else {}
    target_meta = incoming_meta if incoming_meta is meta and isinstance(meta, dict) else dict(incoming_meta)
    incoming_action = None
    if isinstance(incoming_meta, dict):
        incoming_action = incoming_meta.get("action")

    labels = classify(seed_text, target_meta)
    dialog_nlu = dict(labels)

    if telemetry:
        telemetry.log_intent(dialog_nlu)
        telemetry.log_entities(dialog_nlu.get("entities"))
        telemetry.log_guardrail(decision="allow")

    if not isinstance(target_meta.get("nlu"), dict):
        target_meta["nlu"] = dict(dialog_nlu)
    else:
        try:
            target_meta["nlu"].update(dialog_nlu)
        except Exception:
            target_meta["nlu"] = dict(dialog_nlu)
    target_meta[_DIALOG_NLU_KEY] = dict(dialog_nlu)

    existing_universal = target_meta.get("universal")
    if isinstance(existing_universal, dict):
        universal = _ensure_universal_fields(existing_universal)
    else:
        try:
            universal = _interpret_universal(
                seed_text,
                meta=target_meta,
                dialog_nlu=dialog_nlu,
                config=cfg,
            )
        except Exception:
            universal = _ensure_universal_fields(None)
    target_meta["universal"] = universal

    session_id = _extract_session_id(target_meta)
    session_goal = None
    if session_id:
        try:
            session_goal = db.update_session_goal(
                session_id,
                phase=universal.get("phase"),
                depth=universal.get("depth"),
                delivery_pref=universal.get("delivery_pref"),
                working_intent=dialog_nlu.get("intent") or universal.get("intent_hint"),
                entities=universal.get("entities"),
            )
        except Exception:
            session_goal = db.get_session_goal(session_id)
        if session_goal:
            cleaned_goal = _clean_session_goal(session_goal)
            target_meta["session_goal"] = copy.deepcopy(cleaned_goal)
            admin_cb = _admin_emit if callable(_admin_emit) else None
            if admin_cb:
                payload = copy.deepcopy(cleaned_goal)
                payload["event"] = "session_goal"
                payload["session_id"] = session_id or target_meta.get("session_id")
                turn_id_value = target_meta.get("turn_id")
                if not turn_id_value and isinstance(incoming_meta, dict):
                    turn_id_value = incoming_meta.get("turn_id")
                if turn_id_value:
                    payload["turn_id"] = turn_id_value
                depth_label = payload.get("depth")
                try:
                    depth_label_str = str(depth_label).strip() if depth_label is not None else ""
                except Exception:
                    depth_label_str = ""
                label_value = f"session_goal:{depth_label_str or 'unset'}"
                try:
                    admin_cb("session_goal", label=label_value, **payload)
                except Exception:
                    pass
                _broadcast_admin_ws_event("session_goal", payload)

    raw_policy = pick_dialog_policy(dialog_nlu, universal, session_goal=session_goal) or {}
    policy = dict(raw_policy)
    if policy.get("action") and "teacher_move" not in policy:
        policy["teacher_move"] = policy["action"]

    if telemetry:
        confidence_value: Optional[float]
        try:
            confidence_raw = universal.get("confidence") if isinstance(universal, Mapping) else None
            confidence_value = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else None
        except Exception:
            confidence_value = None
        planner_features = _planner_feature_summary(dialog_nlu, universal)
        telemetry.log_planner_decision(
            confidence=confidence_value,
            band=policy.get("confidence_band"),
            teacher_move=policy.get("teacher_move") or policy.get("action"),
            top_features=planner_features or None,
        )

    action = target_meta.get(_DIALOG_ACTION_KEY) or target_meta.get("action")
    if not action:
        action = policy.get("action") or policy.get("teacher_move") or "respond"
    canonical_action = _ACTION_ALIASES.get(str(action or "").strip().lower(), action)
    target_meta["action"] = canonical_action
    target_meta[_DIALOG_ACTION_KEY] = canonical_action
    if policy.get("frame"):
        target_meta.setdefault("frame", policy.get("frame"))
    else:
        target_meta.setdefault("frame", canonical_action)

    policy_verbosity = str(policy.get("verbosity") or "").strip().lower() or None
    if policy_verbosity and policy_verbosity not in {"brief", "normal"}:
        policy_verbosity = None

    if policy_verbosity:
        target_meta.setdefault(_DIALOG_VERBOSITY_KEY, policy_verbosity)
        target_meta.setdefault("verbosity", policy_verbosity)

    if _DIALOG_VERBOSITY_KEY not in target_meta:
        target_meta[_DIALOG_VERBOSITY_KEY] = cfg.get("gen_target_verbosity", "medium")
    if "verbosity" not in target_meta:
        target_meta["verbosity"] = target_meta[_DIALOG_VERBOSITY_KEY]
    elif _DIALOG_VERBOSITY_KEY not in target_meta:
        target_meta[_DIALOG_VERBOSITY_KEY] = target_meta["verbosity"]

    show_suggestions = _resolve_show_suggestions(target_meta, policy, cfg)
    target_meta["show_suggestions"] = show_suggestions
    target_meta[_DIALOG_SHOW_SUGGESTIONS_KEY] = show_suggestions
    target_meta[_DIALOG_POLICY_KEY] = dict(policy)

    if telemetry:
        policy_move = policy.get("teacher_move") or policy.get("action")
        resolved_move = target_meta.get(_DIALOG_ACTION_KEY)
        override_reason = (
            incoming_meta.get("policy_override_reason") if isinstance(incoming_meta, dict) else None
        )
        if not override_reason:
            if incoming_action and resolved_move and str(incoming_action) != str(resolved_move):
                override_reason = "meta_override"
            elif resolved_move and policy_move and str(resolved_move) != str(policy_move):
                override_reason = "policy_adjustment"
            else:
                override_reason = "policy"
        telemetry.log_teacher_move(
            resolved_move=resolved_move,
            policy_move=policy_move,
            reason=override_reason,
        )
        telemetry.log_toolplan(policy)

    return target_meta, dialog_nlu, policy


def prepare_policy_meta(seed_text: str,
                        meta: Optional[Dict[str, Any]] = None,
                        *,
                        cfg: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Deprecated alias for backwards compatibility."""
    return prepare_turn_metadata(seed_text, meta, cfg=cfg)


def _collect_policy_chips(policy: Optional[Dict[str, Any]]) -> List[str]:
    if not ENABLE_POLICY_CHIPS or not policy:
        return []
    raw = policy.get("chips") if isinstance(policy, dict) else None
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        chips = list(raw)
    else:
        chips = [raw]
    cap = _suggestion_cap()
    cleaned: List[str] = []
    seen = set()
    for item in chips:
        text = _normalize_suggestion_item(item)
        if not text:
            continue
        if len(text) > 50:
            text = text[:50].rstrip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
        if len(cleaned) >= cap:
            break
    return cleaned


def summarize_policy_metadata(policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract lightweight policy hints for downstream consumers."""
    summary: Dict[str, Any] = {}
    chips = _collect_policy_chips(policy)
    if chips:
        summary["policy_chips"] = chips

    band: Optional[str] = None
    if isinstance(policy, dict):
        raw_band = policy.get("confidence_band")
        if isinstance(raw_band, str) and raw_band.strip():
            band = raw_band.strip()
        else:
            goal_meta = policy.get("goal_metadata")
            if isinstance(goal_meta, dict):
                goal_band = goal_meta.get("planner_confidence_band") or goal_meta.get(
                    "confidence_band"
                )
                if isinstance(goal_band, str) and goal_band.strip():
                    band = goal_band.strip()
    if band:
        summary["policy_confidence_band"] = band
        summary.setdefault("planner_confidence_band", band)
    return summary


def _coerce_bool_flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        return None
    try:
        return bool(value)
    except Exception:
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except Exception:
            return None
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _normalize_toggle_for_log(value: Any) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered:
            return None
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        try:
            return float(lowered)
        except Exception:
            return value.strip()
    try:
        return bool(value)
    except Exception:
        return None


def _extract_toggle_from_cfg(cfg: Optional[Dict[str, Any]], name: str) -> Optional[Any]:
    if not isinstance(cfg, dict):
        return None
    for key in (f"gen_{name}", name, f"{name}_level", f"allow_{name}", f"{name}_enabled"):
        if key in cfg:
            return cfg.get(key)
    features = cfg.get("features") if isinstance(cfg.get("features"), dict) else None
    if isinstance(features, dict):
        for key in (name, f"{name}_enabled"):
            if key in features:
                return features.get(key)
    return None


def _format_toggle_element(name: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if abs(float(value)) < 1e-6:
            return None
        return f"{name}:{float(value):.2f}"
    if isinstance(value, bool):
        return name if value else None
    text = str(value).strip()
    if not text:
        return None
    return f"{name}:{text}"


def _collect_guardrail_list(meta: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(meta, dict):
        return []

    muted: List[str] = []

    def _extend(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            val = value.strip()
            if val:
                muted.append(val)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _extend(item)
            return
        if isinstance(value, dict):
            for key, flag in value.items():
                if isinstance(flag, bool) and not flag:
                    continue
                if flag:
                    muted.append(str(key))
            return
        try:
            if bool(value):
                muted.append(str(value))
        except Exception:
            pass

    for key in (
        "persona_guardrail_muted",
        "persona_guardrail_suppressed",
        "guardrail_persona_muted",
        "guardrail_persona_suppressed",
    ):
        _extend(meta.get(key))

    guardrail_section = meta.get("guardrail") or meta.get("guardrails")
    if isinstance(guardrail_section, dict):
        for subkey in ("persona", "persona_muted", "muted"):
            _extend(guardrail_section.get(subkey))

    return sorted({m for m in muted if m})


def _summarize_persona_trace(trace: Optional[Dict[str, Any]],
                             cfg: Optional[Dict[str, Any]],
                             *,
                             persona: Optional[Dict[str, Any]] = None,
                             meta: Optional[Dict[str, Any]] = None) -> Tuple[float, List[str], List[str]]:
    trace = trace or {}
    persona_level = _coerce_float(trace.get("intensity"))
    if persona_level is None and persona:
        persona_level = _coerce_float(persona.get("intensity") or persona.get("nebraska_persona_level"))
    if persona_level is None and cfg:
        persona_level = _coerce_float(cfg.get("nebraska_persona_level"))
    if persona_level is None:
        persona_level = 0.0
    persona_level = max(0.0, min(1.0, persona_level))

    quote_meta = trace.get("quote") or {}
    quote_text = str(quote_meta.get("text") or "").strip()
    quote_id = quote_meta.get("quote_id")
    if not quote_id and quote_text:
        bank = quote_meta.get("bank")
        salted = f"{bank or ''}::{quote_text}" if bank else quote_text
        quote_id = hashlib.sha1(salted.encode("utf-8", errors="ignore")).hexdigest()[:12]

    toggles: Dict[str, Any] = {}
    if isinstance(trace.get("toggles"), dict):
        toggles.update(trace.get("toggles"))
    for name in ("humor", "metaphor"):
        if name not in toggles:
            cfg_value = _extract_toggle_from_cfg(cfg, name)
            normalized = _normalize_toggle_for_log(cfg_value)
            if normalized is not None:
                toggles[name] = normalized

    persona_elements: List[str] = []
    if quote_text and quote_id:
        persona_elements.append(f"quote:{quote_id}")
    for name in ("humor", "metaphor"):
        formatted = _format_toggle_element(name, toggles.get(name))
        if formatted:
            persona_elements.append(formatted)

    guardrails: List[str] = []
    guardrail_meta = trace.get("guardrail") or {}
    trace_muted = guardrail_meta.get("muted") if isinstance(guardrail_meta, dict) else None
    if isinstance(trace_muted, (list, tuple, set)):
        guardrails.extend(str(item) for item in trace_muted if item)
    elif trace_muted:
        guardrails.append(str(trace_muted))

    guardrails.extend(_collect_guardrail_list(meta))

    quote_enabled_flag = quote_meta.get("enabled")
    candidate_count = quote_meta.get("candidate_count")
    try:
        candidate_count_val = int(candidate_count)
    except Exception:
        candidate_count_val = 0
    if quote_enabled_flag is False and (candidate_count_val or quote_meta.get("bank")):
        guardrails.append("quote")

    cfg_quote_enabled = _coerce_bool_flag(cfg.get("nebraska_quotes_enabled")) if isinstance(cfg, dict) else None
    if cfg_quote_enabled is False:
        guardrails.append("quote")

    persona_elements = sorted({elem for elem in persona_elements if elem})
    guardrails = sorted({g for g in guardrails if g})

    return persona_level, persona_elements, guardrails


def _log_persona_applied(turn_id: str,
                         trace: Optional[Dict[str, Any]],
                         cfg: Optional[Dict[str, Any]],
                         *,
                         persona: Optional[Dict[str, Any]] = None,
                         meta: Optional[Dict[str, Any]] = None) -> None:
    try:
        persona_level, persona_elements, guardrails = _summarize_persona_trace(
            trace,
            cfg,
            persona=persona,
            meta=meta,
        )
        _jlog(
            "persona_applied",
            turn_id=str(turn_id),
            persona_level=persona_level,
            persona_elements=persona_elements,
            guardrails_suppressed=guardrails,
        )
    except Exception:
        pass


def _foundation_backoff_active(now: Optional[float] = None) -> bool:
    global _FOUNDATION_DISABLED_UNTIL, _FOUNDATION_LAST_ERROR
    if now is None:
        now = _t.time()
    with _FOUNDATION_FAILURE_LOCK:
        if _FOUNDATION_DISABLED_UNTIL <= 0:
            return False
        if now >= _FOUNDATION_DISABLED_UNTIL:
            _FOUNDATION_DISABLED_UNTIL = 0.0
            _FOUNDATION_LAST_ERROR = None
            return False
        return True


def _note_foundation_failure(exc: Optional[BaseException] = None,
                             *,
                             backoff_seconds: Optional[float] = None) -> None:
    global _FOUNDATION_DISABLED_UNTIL, _FOUNDATION_LAST_ERROR
    duration = _FOUNDATION_BACKOFF_SECONDS if backoff_seconds is None else float(backoff_seconds or 0.0)
    now = _t.time()
    with _FOUNDATION_FAILURE_LOCK:
        if duration <= 0:
            _FOUNDATION_DISABLED_UNTIL = now
        else:
            _FOUNDATION_DISABLED_UNTIL = now + duration
        if exc is not None:
            _FOUNDATION_LAST_ERROR = exc.__class__.__name__


def _clear_foundation_failure() -> None:
    global _FOUNDATION_DISABLED_UNTIL, _FOUNDATION_LAST_ERROR
    with _FOUNDATION_FAILURE_LOCK:
        _FOUNDATION_DISABLED_UNTIL = 0.0
        _FOUNDATION_LAST_ERROR = None


def _should_skip_legacy(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    if bool(meta.get("skip_legacy_fallback")):
        return True
    source = str(meta.get("source", "") or "").strip().lower()
    if source in _WS_SOURCES:
        return True
    return False


def _allocate_turn_id(force_turn_id: Optional[str]) -> str:
    if force_turn_id is not None:
        db.memory['turn_seq'] = db.memory.setdefault('turn_seq', 0) + 1
        return str(force_turn_id)
    turn_seq = db.memory.setdefault('turn_seq', 0) + 1
    db.memory['turn_seq'] = turn_seq
    return str(turn_seq)


def make_assistant_frames(seed_text: str,
                          session_id: str,
                          meta: Optional[Dict] = None,
                          correlation_user_msg_id: Optional[str] = None,
                          *,
                          force_turn_id: Optional[str] = None,
                          broadcast_immediately: bool = True,
                          cfg: Optional[Dict[str, Any]] = None,
                          telemetry: Optional[NluLoggingContext] = None) -> Tuple[Optional[str], List[Dict]]:
    """
    Produce assistant frames for a given user seed text using the configured LLM provider.
    ALWAYS returns a frames list that includes at least:
        - one 'assistant_chunk' (with either model text OR a safe fallback), and
        - one 'assistant_end'.
    When broadcast_immediately is True (default) frames are broadcast to the WS bus.
    """
    cfg = cfg or db.get_config()
    meta = dict(meta or {})
    if session_id:
        meta.setdefault("session_id", session_id)
        meta.setdefault("sid", session_id)
    turn_id = _allocate_turn_id(force_turn_id)

    context = telemetry or _create_nlu_context(
        turn_id=turn_id,
        session_id=session_id,
        correlation_user_msg_id=correlation_user_msg_id,
        meta=meta if isinstance(meta, dict) else {},
        settings=SETTINGS,
        cfg=cfg,
    )
    context.turn_id = str(turn_id)

    context.log_start(seed_text, meta=meta if isinstance(meta, dict) else {})

    try:
        classified_labels: Dict[str, Any]
        classified_policy: Dict[str, Any]
        skip_prepare = False
        existing_policy: Optional[Dict[str, Any]] = None
        existing_labels: Optional[Dict[str, Any]] = None

        if isinstance(meta, dict):
            raw_policy = meta.get(_DIALOG_POLICY_KEY) or meta.get("policy")
            if isinstance(raw_policy, dict):
                existing_policy = dict(raw_policy)
            raw_labels = meta.get(_DIALOG_NLU_KEY)
            if not isinstance(raw_labels, dict):
                raw_labels = meta.get("nlu") if isinstance(meta.get("nlu"), dict) else None
            if isinstance(raw_labels, dict):
                existing_labels = dict(raw_labels)
            if existing_policy is not None and existing_labels is not None:
                skip_prepare = True
                meta[_DIALOG_POLICY_KEY] = dict(existing_policy)
                if isinstance(meta.get("nlu"), dict):
                    try:
                        meta["nlu"].update(existing_labels)
                    except Exception:
                        meta["nlu"] = dict(existing_labels)
                else:
                    meta["nlu"] = dict(existing_labels)
                meta[_DIALOG_NLU_KEY] = dict(existing_labels)

        if not skip_prepare:
            meta, classified_labels, classified_policy = prepare_turn_metadata(
                seed_text,
                meta,
                cfg=cfg,
                telemetry=context,
            )
        else:
            classified_labels = dict(existing_labels or {})
            classified_policy = dict(existing_policy or {})
            if context:
                context.log_intent(classified_labels)
                context.log_entities(classified_labels.get("entities"))
                context.log_guardrail(decision="allow")
        skip_legacy = _should_skip_legacy(meta)

        action = _normalize_action(meta)
        if isinstance(meta, dict):
            meta['action'] = action
            meta[_DIALOG_ACTION_KEY] = action

        try:
            source = str(meta.get("source", "") or "").strip().lower()
        except Exception:
            source = ""
        try:
            is_greet = str(seed_text or "").strip().lower() == "greet"
        except Exception:
            is_greet = False
        if not is_greet and source:
            is_greet = source in {"greet", "ws_greet", "greet_fallback"}

        fallback_line = str(cfg.get("assistant_fallback_line") or "Hi! I’m ready to help.")

        def _cfg_flag(key: str, default: bool) -> bool:
            val = meta.get(key)
            if val is None:
                val = cfg.get(key, default)
            try:
                if isinstance(val, str):
                    return val.strip().lower() in ("1", "true", "yes", "on")
                return bool(val)
            except Exception:
                return default

        fallback_on_empty = _cfg_flag("assistant_fallback_on_empty", True)
        fallback_on_error = _cfg_flag("assistant_fallback_on_error", True)
        fallback_emit_event = _cfg_flag("assistant_fallback_emit_event", True)

        if _should_use_foundation(seed_text):
            try:
                return _make_foundation_frames(
                    seed_text,
                    session_id,
                    meta,
                    cfg,
                    correlation_user_msg_id=correlation_user_msg_id,
                    turn_id=turn_id,
                    telemetry=context,
                    is_greet=is_greet,
                    fallback_line=fallback_line,
                    fallback_on_empty=fallback_on_empty,
                    fallback_on_error=fallback_on_error,
                    fallback_emit_event=fallback_emit_event,
                    action=action,
                    broadcast_immediately=broadcast_immediately,
                )
            except Exception as e:
                context.log_error("foundation_pipeline_error", str(e))
                _note_foundation_failure(e)
                skip_legacy = False
                if isinstance(meta, dict):
                    meta.pop("skip_legacy_fallback", None)
                error_payload = {"session_id": session_id, "error": e.__class__.__name__}
                try:
                    _admin_emit('foundation_pipeline_error', **error_payload)
                except Exception:
                    pass
                _broadcast_admin_ws_event('foundation_pipeline_error', error_payload)

        if skip_legacy:
            return None, []

        return _make_legacy_frames(
            seed_text,
            session_id,
            meta,
            cfg,
            correlation_user_msg_id=correlation_user_msg_id,
            turn_id=turn_id,
            telemetry=context,
            is_greet=is_greet,
            fallback_line=fallback_line,
            fallback_on_empty=fallback_on_empty,
            fallback_on_error=fallback_on_error,
            fallback_emit_event=fallback_emit_event,
            action=action,
            labels=classified_labels,
            policy=classified_policy,
            broadcast_immediately=broadcast_immediately,
        )
    except Exception as exc:
        context.log_error("streaming_pipeline_error", str(exc))
        raise
    finally:
        context.log_done()


def _make_foundation_frames(seed_text: str,
                            session_id: str,
                            meta: Dict[str, Any],
                            cfg: Dict[str, Any],
                            *,
                            correlation_user_msg_id: Optional[str],
                            turn_id: Optional[str] = None,
                            telemetry: Optional[NluLoggingContext] = None,
                            force_turn_id: Optional[str] = None,
                            is_greet: bool,
                            fallback_line: str,
                            fallback_on_empty: bool,
                            fallback_on_error: bool,
                            fallback_emit_event: bool,
                            action: Optional[str] = None,
                            broadcast_immediately: bool = True) -> Tuple[str, List[Dict]]:
    if turn_id is None:
        turn_id = _allocate_turn_id(force_turn_id)
    turn_id = str(turn_id)

    phase_label = "greet" if is_greet else "turn"
    llm_tracker = make_llm_flow_tracker(None, phase=phase_label)

    if telemetry is None:
        telemetry = _create_nlu_context(
            turn_id=turn_id,
            session_id=session_id,
            correlation_user_msg_id=correlation_user_msg_id,
            meta=meta,
            settings=SETTINGS,
            cfg=cfg,
        )
        telemetry.turn_id = turn_id
        telemetry.log_start(seed_text, meta=meta)
    else:
        telemetry.turn_id = turn_id
    store = PersonaStore()
    persona = PersonaManager(store).get_active()
    dialog_meta = dict(meta or {})

    nlu_result = _nlu.infer(seed_text, persona['id'], dialog_meta, store)
    policy = _nlu.policy.decide(nlu_result, nlu_result.get('tags', {}), persona['id'], store) or {}
    policy_summary = summarize_policy_metadata(policy)
    if isinstance(policy, dict):
        for key in ("confidence_band", "clarify_variant"):
            value = policy.get(key)
            existing = meta.get(key) if isinstance(meta, dict) else None
            chosen = existing or value
            if value is not None and existing in (None, ""):
                meta[key] = value
                chosen = value
            if chosen is not None and key not in dialog_meta:
                dialog_meta[key] = chosen
    if policy_summary and isinstance(meta, dict):
        for key, value in policy_summary.items():
            meta.setdefault(key, value)

    telemetry.log_entities(nlu_result.get("entities"))

    try:
        nlu_meta = dict(nlu_result)
    except Exception:
        nlu_meta = {}
    else:
        meta["nlu"] = nlu_meta
        meta[_DIALOG_NLU_KEY] = dict(nlu_meta)

    provided_action: Optional[str] = None
    if action is not None:
        try:
            provided_action = str(action or "").strip().lower()
        except Exception:
            provided_action = None
    normalized_action = provided_action or _normalize_action(meta)
    policy_move = _policy_action(policy)
    clarifier_style: Optional[str] = None
    band_flag = str(dialog_meta.get('confidence_band') or meta.get('confidence_band') or "").strip().lower()
    variant_flag = str(dialog_meta.get('clarify_variant') or meta.get('clarify_variant') or "").strip().lower()
    existing_style = str(meta.get('clarifier_style') or dialog_meta.get('clarifier_style') or "").strip().lower()
    if policy_move == "clarify" and (band_flag == "low" or variant_flag == "high_level"):
        clarifier_style = "clarifier_high_level"
    elif existing_style == "clarifier_high_level":
        clarifier_style = existing_style
    if clarifier_style:
        meta['clarifier_style'] = clarifier_style
        dialog_meta['clarifier_style'] = clarifier_style
    if not provided_action and policy_move and not normalized_action:
        normalized_action = str(policy_move or "").strip().lower() or normalized_action
    if normalized_action:
        meta['action'] = normalized_action
        meta[_DIALOG_ACTION_KEY] = normalized_action
    if _DIALOG_VERBOSITY_KEY not in meta:
        meta[_DIALOG_VERBOSITY_KEY] = cfg.get('gen_target_verbosity', 'medium')
    if 'verbosity' not in meta:
        meta['verbosity'] = meta[_DIALOG_VERBOSITY_KEY]
    elif _DIALOG_VERBOSITY_KEY not in meta:
        meta[_DIALOG_VERBOSITY_KEY] = meta['verbosity']

    def _coerce_dialog_flag(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return None
        try:
            return bool(value)
        except Exception:
            return None

    existing_pref = None
    if isinstance(meta, dict):
        if "show_suggestions" in meta:
            existing_pref = _coerce_dialog_flag(meta.get("show_suggestions"))
        elif _DIALOG_SHOW_SUGGESTIONS_KEY in meta:
            existing_pref = _coerce_dialog_flag(meta.get(_DIALOG_SHOW_SUGGESTIONS_KEY))

    if existing_pref is None:
        show_suggestions = _resolve_show_suggestions(meta, policy, cfg)
    else:
        show_suggestions = bool(existing_pref)

    meta['show_suggestions'] = show_suggestions
    meta[_DIALOG_SHOW_SUGGESTIONS_KEY] = show_suggestions
    meta[_DIALOG_POLICY_KEY] = dict(policy)

    prompt_meta = dict(dialog_meta)
    prompt_meta['intent'] = nlu_result.get('intent')
    policy_action = _policy_action(policy)
    if policy_action:
        prompt_meta['teacher_move'] = policy_action

    examples = store.match_examples(persona['id'], seed_text)
    messages, teacher_move, prompt_hash, persona_trace = build_messages(
        persona=persona,
        user_text=seed_text,
        dialog_meta=prompt_meta,
        examples=examples,
    )

    _log_persona_applied(
        turn_id,
        persona_trace,
        cfg,
        persona=persona,
        meta=meta,
    )

    kb_count = 0
    try:
        kb_candidates = meta.get('kb_snippets') if isinstance(meta, dict) else None
        if isinstance(kb_candidates, (list, tuple)):
            kb_count = len(kb_candidates)
    except Exception:
        kb_count = 0
    telemetry.log_prompt_summary(messages=messages, prompt_hash=prompt_hash, kb_count=kb_count)

    use_llm = True
    llm_info: Dict[str, Any] = {}
    if use_llm:
        model_name = _resolve_foundation_model(cfg)
        llm_tracker = make_llm_flow_tracker(
            session_id,
            phase=phase_label,
            turn_id=turn_id,
            model=model_name,
        )
        llm_tracker.start()

        def _llm_state_snapshot() -> Dict[str, Any]:
            return {
                "phase": phase_label,
                "tts_active": None,
                "confirm_open": None,
                "asr_ready": None,
                "last_partial_age_ms": None,
                "queue": None,
            }
        try:
            foundation_result = _call_foundation_with_retry(messages, cfg, telemetry=telemetry)
        except Exception as exc:
            llm_tracker.error(exc.__class__.__name__, state=_llm_state_snapshot())
            llm_tracker.final(text="")
            telemetry.log_error("foundation_call_error", str(exc))
            raise
        else:
            _clear_foundation_failure()
        if isinstance(foundation_result, tuple) and len(foundation_result) == 2:
            reply, llm_info = foundation_result
        else:
            reply = foundation_result
            llm_info = {}
    fallback_fired = False
    fallback_reason: Optional[str] = None

    if use_llm:
        if fallback_on_error and not reply and not fallback_fired:
            # Foundation path has no explicit error flag; treat empty as error when allowed.
            reply = fallback_line
            fallback_fired = True
            fallback_reason = "empty"
        elif fallback_on_empty and not (reply or "").strip():
            reply = fallback_line
            fallback_fired = True
            fallback_reason = "empty"

    safe_reply = _scrub_debug_stamps(reply)

    if use_llm and not fallback_fired:
        shaped = _apply_action_shape(normalized_action, safe_reply, meta)
        if shaped:
            safe_reply = shaped
        else:
            safe_reply = ""

    if is_greet:
        safe_reply = _short_greeting(safe_reply)
    elif fallback_fired:
        safe_reply = _short_greeting(safe_reply)

    if not (safe_reply or "").strip():
        use_configured_fallback = fallback_fired or (use_llm and fallback_on_empty)
        if not use_llm:
            use_configured_fallback = True
        if use_configured_fallback:
            safe_reply = _scrub_debug_stamps(fallback_line)
            if not safe_reply.strip():
                safe_reply = fallback_line.strip() or _LEGACY_WARMUP_LINE
            if not fallback_fired:
                fallback_fired = True
                if not fallback_reason:
                    fallback_reason = "empty"
            elif not fallback_reason:
                fallback_reason = "empty"
        else:
            safe_reply = _LEGACY_WARMUP_LINE

    telemetry.mark_fallback(fallback_fired, fallback_reason)

    # Personalize greet: name + dynamic goal ask
    if is_greet:
        meta.setdefault("session_id", session_id)
        meta.setdefault("turn_id", turn_id)
        safe_reply = _personalize_greet(safe_reply, meta)
    if use_llm:
        preview_text = llm_info.get("preview")
        if not preview_text:
            try:
                preview_text = " ".join((safe_reply or "").split()[:8])
            except Exception:
                preview_text = None
        telemetry.log_llm_response(
            output_tokens=llm_info.get("output_tokens"),
            finish_reason=llm_info.get("finish_reason"),
            preview=preview_text,
            fallback_fired=fallback_fired,
            fallback_reason=fallback_reason,
        )
        llm_tracker.final(tokens_out=llm_info.get("output_tokens"), text=safe_reply)

    policy_chips = _collect_policy_chips(policy)

    try:
        _jlog(
            "turn_telemetry",
            sid=session_id,
            intent=nlu_result.get('intent'),
            confidence=nlu_result.get('confidence'),
            teacher_move=policy_action or teacher_move,
            chips=len(policy_chips),
            prompt_hash=prompt_hash,
        )
    except Exception:
        pass

    if fallback_fired and fallback_emit_event:
        fallback_payload: Dict[str, Any] = {
            "session_id": session_id,
            "turn_id": turn_id,
            "reason": fallback_reason or 'unknown',
        }
        try:
            _admin_emit('fallback', **fallback_payload)
        except Exception:
            pass
        _broadcast_admin_ws_event('fallback', fallback_payload)

    _emit_turn_action_metadata(turn_id, meta, is_greet=is_greet, session_id=session_id)

    frames: List[Dict] = []
    active_teacher_move = policy_action or teacher_move
    if _should_force_welcome_move(meta, is_greet=is_greet, intent=nlu_result.get('intent')):
        active_teacher_move = _WELCOME_MOVE
    kb_source = kb_candidates if 'kb_candidates' in locals() else None
    _log_policy_decision(
        session_id=session_id,
        turn_id=turn_id,
        resolved_move=active_teacher_move,
        normalized_action=normalized_action,
        policy_move=policy_action,
        teacher_move_seed=teacher_move,
        fallback_fired=fallback_fired,
        fallback_reason=fallback_reason,
        meta=meta,
        nlu_meta=nlu_result,
        used_docs_source=kb_source,
    )
    chunk: Dict[str, Any] = {
        'type': 'assistant_chunk',
        'turn_id': turn_id,
        'text': safe_reply,
        'kb_hits': 0,
        'intent': nlu_result.get('intent'),
        'teacher_move': active_teacher_move,
    }
    if policy_summary:
        band = policy_summary.get('planner_confidence_band') or policy_summary.get('policy_confidence_band')
        if band:
            chunk['planner_confidence_band'] = band
        policy_band = policy_summary.get('policy_confidence_band')
        if policy_band and policy_band != band:
            chunk['policy_confidence_band'] = policy_band
        chips = policy_summary.get('policy_chips')
        if chips:
            chunk['policy_chips'] = chips
    if isinstance(meta, dict):
        clarify_variant = meta.get('clarify_variant')
        clarifier_style = meta.get('clarifier_style')
        if clarifier_style:
            chunk['clarifier_style'] = clarifier_style
        if clarify_variant:
            chunk['clarify_variant'] = clarify_variant
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(chunk)

    legacy_suggestions = []
    try:
        if cfg.get('suggestions_enabled', True):
            legacy_suggestions = hygienic_suggestions(safe_reply)
    except Exception:
        legacy_suggestions = []
    merged_suggestions = merge_suggestions(policy_chips, legacy_suggestions)
    _log_suggestions_made(
        turn_id=turn_id,
        policy_chips=policy_chips,
        legacy_suggestions=legacy_suggestions,
        merged_suggestions=merged_suggestions,
        cfg=cfg,
        session_id=session_id,
    )
    try:
        _jlog(
            "chips_emit",
            sid=session_id,
            intent=nlu_result.get('intent'),
            count=len(merged_suggestions),
            items=merged_suggestions,
        )
    except Exception:
        pass
    allow_suggestions = bool(meta.get('show_suggestions')) if isinstance(meta, dict) else False
    should_emit_suggestions = bool(merged_suggestions) and (is_greet or allow_suggestions)
    if should_emit_suggestions:
        frames.append(
            {
                'type': 'suggestions',
                'turn_id': turn_id,
                'items': build_suggestion_items(merged_suggestions),
            }
        )

    end_fr = {'type': 'assistant_end', 'turn_id': turn_id}
    if correlation_user_msg_id:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    if broadcast_immediately:
        _broadcast_frames(session_id, frames, turn_id)
    return turn_id, frames


def _make_legacy_frames(seed_text: str,
                        session_id: str,
                        meta: Dict[str, Any],
                        cfg: Dict[str, Any],
                        *,
                        correlation_user_msg_id: Optional[str],
                        turn_id: Optional[str] = None,
                        telemetry: Optional[NluLoggingContext] = None,
                        force_turn_id: Optional[str] = None,
                        is_greet: bool,
                        fallback_line: str,
                        fallback_on_empty: bool,
                        fallback_on_error: bool,
                        fallback_emit_event: bool,
                        action: Optional[str] = None,
                        labels: Optional[Dict[str, Any]] = None,
                        policy: Optional[Dict[str, Any]] = None,
                        broadcast_immediately: bool = True) -> Tuple[str, List[Dict]]:
    persona = _get_persona_for_session(session_id)

    if turn_id is None:
        turn_id = _allocate_turn_id(force_turn_id)
    turn_id = str(turn_id)

    phase_label = "greet" if is_greet else "turn"
    llm_tracker = make_llm_flow_tracker(None, phase=phase_label)

    persona_trace = {
        "intensity": (persona or {}).get("intensity") or (persona or {}).get("nebraska_persona_level"),
        "quote": {
            "text": "",
            "quote_id": None,
            "enabled": _coerce_bool_flag((cfg or {}).get("nebraska_quotes_enabled")) if isinstance(cfg, dict) else None,
            "picked": False,
            "candidate_count": 0,
            "bank": None,
        },
        "toggles": {},
        "guardrail": {},
    }

    if telemetry is None:
        telemetry = _create_nlu_context(
            turn_id=turn_id,
            session_id=session_id,
            correlation_user_msg_id=correlation_user_msg_id,
            meta=meta,
            settings=SETTINGS,
            cfg=cfg,
        )
        telemetry.turn_id = turn_id
        telemetry.log_start(seed_text, meta=meta)
    else:
        telemetry.turn_id = turn_id

    if labels is None:
        labels = classify(seed_text, meta)
    else:
        labels = dict(labels)
    telemetry.log_entities(labels.get("entities"))
    _log_persona_applied(
        turn_id,
        persona_trace,
        cfg,
        persona=persona,
        meta=meta,
    )
    if policy is None:
        policy = pick_dialog_policy(labels) or {}
    else:
        policy = dict(policy or {})

    policy_summary = summarize_policy_metadata(policy)
    if policy_summary and isinstance(meta, dict):
        for key, value in policy_summary.items():
            meta.setdefault(key, value)

    try:
        if isinstance(meta, dict):
            meta_nlu = meta.setdefault('nlu', {})
            meta_nlu.update(labels)
            dialog_nlu = meta.setdefault(_DIALOG_NLU_KEY, {})
            try:
                dialog_nlu.update(labels)
            except Exception:
                meta[_DIALOG_NLU_KEY] = dict(labels)
    except Exception:
        pass
    policy_action = _policy_action(policy)
    teacher_move = policy_action

    provided_action: Optional[str] = None
    if action is not None:
        try:
            provided_action = str(action or "").strip().lower()
        except Exception:
            provided_action = None
    normalized_action = provided_action or _normalize_action(meta)
    if not provided_action and policy_action and not normalized_action:
        normalized_action = str(policy_action or "").strip().lower() or normalized_action
    if isinstance(meta, dict):
        if normalized_action:
            meta['action'] = normalized_action
            meta[_DIALOG_ACTION_KEY] = normalized_action
        if _DIALOG_VERBOSITY_KEY not in meta:
            if 'verbosity' in meta:
                meta[_DIALOG_VERBOSITY_KEY] = meta.get('verbosity')
            else:
                meta[_DIALOG_VERBOSITY_KEY] = cfg.get('gen_target_verbosity', 'medium')
        if 'verbosity' not in meta:
            meta['verbosity'] = meta[_DIALOG_VERBOSITY_KEY]

        def _coerce_dialog_flag(value: Any) -> Optional[bool]:
            if value is None:
                return None
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
                return None
            try:
                return bool(value)
            except Exception:
                return None

        existing_pref = None
        if 'show_suggestions' in meta:
            existing_pref = _coerce_dialog_flag(meta.get('show_suggestions'))
        elif _DIALOG_SHOW_SUGGESTIONS_KEY in meta:
            existing_pref = _coerce_dialog_flag(meta.get(_DIALOG_SHOW_SUGGESTIONS_KEY))

        if existing_pref is None:
            show_suggestions = _resolve_show_suggestions(meta, policy, cfg)
        else:
            show_suggestions = bool(existing_pref)

        meta['show_suggestions'] = show_suggestions
        meta[_DIALOG_SHOW_SUGGESTIONS_KEY] = show_suggestions
        meta[_DIALOG_POLICY_KEY] = dict(policy)

    kb: List[str] = []
    try:
        if cfg.get('kb_enabled', False):
            kb = kb_search(seed_text, top_k=int(cfg.get('kb_top_k', 3)))
    except Exception:
        kb = []

    prompt = _build_prompt(seed_text, persona, kb, teacher_move)
    try:
        prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]
    except Exception:
        prompt_hash = None
    telemetry.log_prompt_summary(messages=None, prompt_hash=prompt_hash, kb_count=len(kb))

    reply: str
    error_note: Optional[str] = None
    use_llm = True

    if use_llm:
        try:
            provider = get_provider(cfg)
        except Exception as e:
            provider = None
            error_note = "llm_not_available"
            provider_error_payload = {
                "session_id": session_id,
                "error": e.__class__.__name__,
            }
            try:
                _admin_emit("llm_provider_error", **provider_error_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event("llm_provider_error", provider_error_payload)
            telemetry.log_error("llm_not_available", str(e))
        if provider is not None:
            model_name = _resolve_provider_model(provider)
            llm_tracker = make_llm_flow_tracker(
                session_id,
                phase=phase_label,
                turn_id=turn_id,
                model=model_name,
            )
            llm_tracker.start()

            def _llm_state_snapshot() -> Dict[str, Any]:
                return {
                    "phase": phase_label,
                    "tts_active": None,
                    "confirm_open": None,
                    "asr_ready": None,
                    "last_partial_age_ms": None,
                    "queue": None,
                }
            try:
                provider_context: Dict[str, Any] = {'kb': kb}
                for key in ('confidence_band', 'clarify_variant', 'clarifier_style'):
                    if isinstance(meta, dict):
                        value = meta.get(key)
                    else:
                        value = None
                    if value:
                        provider_context[key] = value
                if isinstance(meta, dict):
                    dialog_subset = {
                        key: meta.get(key)
                        for key in ('confidence_band', 'clarify_variant', 'clarifier_style')
                        if meta.get(key)
                    }
                    if dialog_subset:
                        provider_context['dialog_meta'] = dialog_subset
                reply = provider.generate_reply(
                    prompt,
                    persona=persona,
                    teacher_move=teacher_move,
                    context=provider_context,
                )
            except Exception as e:
                llm_tracker.error(e.__class__.__name__, state=_llm_state_snapshot())
                error_note = f"llm_error:{e.__class__.__name__}"
                reply = _LEGACY_WARMUP_LINE
                generate_payload = {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "error": e.__class__.__name__,
                }
                try:
                    _admin_emit("llm_generate_error", **generate_payload)
                except Exception:
                    pass
                _broadcast_admin_ws_event("llm_generate_error", generate_payload)
                telemetry.log_error("llm_generate_error", str(e))
        else:
            reply = "Hi! I’m ready to help."
    else:
        reply = _LEGACY_WARMUP_LINE

    fallback_fired = False
    fallback_reason: Optional[str] = None

    if use_llm:
        if fallback_on_error and error_note:
            reply = fallback_line
            fallback_fired = True
            fallback_reason = error_note
        elif fallback_on_empty and not (reply or "").strip():
            reply = fallback_line
            fallback_fired = True
            fallback_reason = "empty"

    safe_reply = _scrub_debug_stamps(reply)

    if use_llm and not fallback_fired:
        shaped = _apply_action_shape(normalized_action, safe_reply, meta)
        if shaped:
            safe_reply = shaped
        else:
            safe_reply = ""

    if is_greet:
        safe_reply = _short_greeting(safe_reply)
    elif fallback_fired:
        safe_reply = _short_greeting(safe_reply)

    if not (safe_reply or "").strip():
        use_configured_fallback = fallback_fired or (use_llm and fallback_on_empty)
        if not use_llm:
            use_configured_fallback = True
        if use_configured_fallback:
            safe_reply = _scrub_debug_stamps(fallback_line)
            if not safe_reply.strip():
                safe_reply = fallback_line.strip() or _LEGACY_WARMUP_LINE
            if not fallback_fired:
                fallback_fired = True
                if not fallback_reason:
                    fallback_reason = "empty"
            elif not fallback_reason:
                fallback_reason = "empty"
        else:
            safe_reply = _LEGACY_WARMUP_LINE

    telemetry.mark_fallback(fallback_fired, fallback_reason)

    # Personalize greet: name + dynamic goal ask
    if is_greet:
        meta.setdefault("session_id", session_id)
        meta.setdefault("turn_id", turn_id)
        safe_reply = _personalize_greet(safe_reply, meta)

    if use_llm:
        preview = None
        try:
            preview = " ".join((safe_reply or "").split()[:8])
        except Exception:
            preview = None
        telemetry.log_llm_response(
            output_tokens=None,
            finish_reason=error_note,
            preview=preview,
            fallback_fired=fallback_fired,
            fallback_reason=fallback_reason,
        )
        llm_tracker.final(text=safe_reply)

    if fallback_fired and fallback_emit_event:
        fallback_payload = {
            "session_id": session_id,
            "turn_id": turn_id,
            "reason": fallback_reason or 'unknown',
        }
        try:
            _admin_emit('fallback', **fallback_payload)
        except Exception:
            pass
        _broadcast_admin_ws_event('fallback', fallback_payload)

    _emit_turn_action_metadata(turn_id, meta, is_greet=is_greet, session_id=session_id)

    frames: List[Dict] = []

    active_teacher_move = teacher_move
    if _should_force_welcome_move(meta, is_greet=is_greet, intent=(labels or {}).get('intent') if isinstance(labels, dict) else None):
        active_teacher_move = _WELCOME_MOVE
    _log_policy_decision(
        session_id=session_id,
        turn_id=turn_id,
        resolved_move=active_teacher_move,
        normalized_action=normalized_action,
        policy_move=policy_action,
        teacher_move_seed=teacher_move,
        fallback_fired=fallback_fired,
        fallback_reason=fallback_reason,
        meta=meta,
        nlu_meta=labels,
        used_docs_source=kb,
    )
    chunk = {
        'type': 'assistant_chunk',
        'turn_id': turn_id,
        'text': safe_reply,
        'kb_hits': len(kb),
    }
    if isinstance(labels, dict) and labels.get('intent'):
        chunk['intent'] = labels.get('intent')
    if active_teacher_move:
        chunk['teacher_move'] = active_teacher_move
    if policy_summary:
        band = policy_summary.get('planner_confidence_band') or policy_summary.get('policy_confidence_band')
        if band:
            chunk['planner_confidence_band'] = band
        policy_band = policy_summary.get('policy_confidence_band')
        if policy_band and policy_band != band:
            chunk['policy_confidence_band'] = policy_band
        chips = policy_summary.get('policy_chips')
        if chips:
            chunk['policy_chips'] = chips
    if isinstance(meta, dict):
        clarify_variant = meta.get('clarify_variant')
        clarifier_style = meta.get('clarifier_style')
        if clarifier_style:
            chunk['clarifier_style'] = clarifier_style
        if clarify_variant:
            chunk['clarify_variant'] = clarify_variant
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    if error_note:
        chunk['note'] = error_note
    frames.append(chunk)

    legacy_suggestions: List[Any] = []
    try:
        if cfg.get('suggestions_enabled', True):
            legacy_suggestions = hygienic_suggestions(safe_reply)
    except Exception:
        legacy_suggestions = []
    policy_chips = _collect_policy_chips(policy)
    merged_suggestions = merge_suggestions(policy_chips, legacy_suggestions)
    _log_suggestions_made(
        turn_id=turn_id,
        policy_chips=policy_chips,
        legacy_suggestions=legacy_suggestions,
        merged_suggestions=merged_suggestions,
        cfg=cfg,
        session_id=session_id,
    )
    try:
        intent = labels.get('intent') if isinstance(labels, dict) else None
        _jlog(
            "chips_emit",
            sid=session_id,
            intent=intent,
            count=len(merged_suggestions),
            items=merged_suggestions,
        )
    except Exception:
        pass
    allow_suggestions = bool(meta.get('show_suggestions')) if isinstance(meta, dict) else False
    should_emit_suggestions = bool(merged_suggestions) and (is_greet or allow_suggestions)
    if should_emit_suggestions:
        frames.append(
            {
                'type': 'suggestions',
                'turn_id': turn_id,
                'items': build_suggestion_items(merged_suggestions),
            }
        )

    end_fr = {'type': 'assistant_end', 'turn_id': turn_id}
    if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    if broadcast_immediately:
        _broadcast_frames(session_id, frames, turn_id)
    return turn_id, frames


# --- MIME mapping helper ------------------------------------------------------

def _guess_tts_mime(output_format: str | None) -> str:
    """
    Map configured TTS output format to an appropriate MIME/container for MSE playback.
    We keep this conservative to avoid mismatches with provider bytes.
    """
    fmt = (output_format or os.environ.get('ELEVEN_OUTPUT_FORMAT') or '').lower()
    # Prefer actual, common provider defaults. If unknown, assume MP3 to avoid mismatches.
    if 'webm' in fmt and 'opus' in fmt:
        return 'audio/webm; codecs="opus"'
    if 'ogg' in fmt and 'opus' in fmt:
        return 'audio/ogg; codecs="opus"'
    if 'mp3' in fmt:
        return 'audio/mpeg'
    if 'wav' in fmt or 'pcm' in fmt or 'l16' in fmt:
        return 'audio/wav'
    # Fallback to MP3 (widely supported; safer than claiming WebM if provider returned MP3)
    return 'audio/mpeg'


# --- WS TTS scheduling (audio over WS) ---------------------------------------
def _tts_text_markers(text: str) -> Dict[str, Any]:
    """Privacy-safe markers for logging text identity."""
    normalized: str
    if isinstance(text, str):
        normalized = text
    else:
        normalized = str(text or "")
    try:
        encoded = normalized.encode("utf-8")
    except Exception:
        encoded = str(normalized).encode("utf-8", "ignore")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "text_hash": digest[:16],
        "text_len": len(normalized),
    }


def schedule_tts_audio(session_id: str,
                       text: str,
                       turn_id: str | None = None,
                       correlation_user_msg_id: Optional[str] = None,
                       audio_bytes: Optional[bytes] = None,
                       chunk_bytes: int = 8192,
                       delay_ms: int = 0,
                       on_complete: Optional[Callable[[], None]] = None,
                       *,
                       on_post_silence: Optional[Callable[[], None]] = None,
                       post_silence_origin: Optional[str] = None,
                       flow_phase: Optional[str] = None,
                       flow_turn_id: Optional[str] = None,
                       flow_include_turn_id: bool = True) -> bool:
    """Synthesize TTS for `text` and stream as WS frames.
    Emits frames like:
        {
          "type": "assistant_audio",
          "turn_id": <str or null>,
          "mime": "<audio mime>",
          "audio_chunks": ["<base64>", ...],
          "is_last": <bool>
        }
    After the final chunk, an explicit:
        { "type": "UtteranceEnd", "turn_id": <str or null> }
    is broadcast to mark audio-complete.
    Non-blocking: runs in a background thread. Returns True if audio streaming
    was scheduled, False if the request was ignored (e.g., audio disabled).
    """
    if not text:
        return False
    cfg = db.get_config()
    feature_audio = bool(cfg.get("feature_audio", True))

    # Track TTS lifecycle for admin truth
    tts_tbl = db.memory.setdefault('tts_status', {})
    sess_tbl = tts_tbl.setdefault(session_id, {})
    turn_key = str(turn_id) if turn_id is not None else 'greet'
    state = sess_tbl.setdefault(turn_key, {'started': False, 'first_chunk': False, 'done': False, 'error': None})

    completion_event: Optional[threading.Event] = None

    safe_turn_id = str(turn_id) if turn_id is not None else None
    text_markers = _tts_text_markers(text)
    audio_override = audio_bytes is not None
    phase_for_flow = flow_phase if flow_phase else ("turn" if safe_turn_id is not None else "greet")
    meta_turn_id = flow_turn_id if flow_turn_id is not None else safe_turn_id
    tracker = make_tts_flow_tracker(
        session_id=session_id,
        phase=phase_for_flow,
        turn_id=meta_turn_id,
        include_turn_id=flow_include_turn_id,
    )

    def _tts_state_snapshot() -> Dict[str, Any]:
        return {
            "phase": phase_for_flow,
            "tts_active": bool(state.get('started')),
            "confirm_open": None,
            "asr_ready": None,
            "last_partial_age_ms": None,
            "queue": None,
        }

    def _log(kind: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {
            "session_id": session_id,
            "sid": session_id,
        }
        if safe_turn_id is not None:
            payload["turn_id"] = safe_turn_id
        if correlation_user_msg_id:
            payload["correlation_user_msg_id"] = correlation_user_msg_id
        payload.update(text_markers)
        payload["audio_override"] = audio_override
        payload.update(fields)
        try:
            _jlog(kind, **payload)
        except Exception:
            pass

    state_snapshot = dict(state)
    level = "warning" if state_snapshot.get('started') or state_snapshot.get('done') else "info"
    _log("tts.schedule", level=level, state=state_snapshot, feature_audio=feature_audio)

    if not feature_audio:
        return False

    desired_mime = _guess_tts_mime(cfg.get('tts_output_format'))

    audio_payload = audio_bytes
    provider_label: Optional[str] = None

    synth_duration_ms = 0

    if audio_payload is None:
        try:
            from app.services.tts_provider import get_tts_provider

            provider = get_tts_provider(cfg or {})
            provider_label = getattr(provider, "name", None) or getattr(type(provider), "__name__", "unknown")
            _log("tts.provider_selected", provider=provider_label, override=False)
        except Exception as e:
            state['error'] = str(e)
            _log("tts.provider.error", error=str(e))
            tracker.error(e.__class__.__name__, state=_tts_state_snapshot())
            error_payload = {
                "session_id": session_id,
                "turn_id": safe_turn_id,
                "error": str(e),
            }
            try:
                _admin_emit('tts:error', **error_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event('tts:error', error_payload)
            return False

        try:
            synth_started_at = _t.time()
            _log("tts.synth.start", desired_mime=desired_mime, chunk_bytes=chunk_bytes, override=False)
            audio_payload, _vis = provider.synth(_scrub_debug_stamps(text))
            state['started'] = True
            synth_duration_ms = int((_t.time() - synth_started_at) * 1000)
            total_audio_bytes = len(audio_payload or b"")
            _log(
                "tts.synth.done",
                desired_mime=desired_mime,
                chunk_bytes=chunk_bytes,
                dur_ms=synth_duration_ms,
                audio_bytes=total_audio_bytes,
                override=False,
            )
        except Exception as e:
            state['error'] = str(e)
            _log("tts.synth.error", error=str(e), override=False)
            tracker.error(e.__class__.__name__, state=_tts_state_snapshot())
            synth_error_payload = {
                "session_id": session_id,
                "turn_id": safe_turn_id,
                "error": str(e),
            }
            try:
                _admin_emit('tts:error', **synth_error_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event('tts:error', synth_error_payload)
            return False
    else:
        provider_label = "prebaked"
        _log("tts.provider_selected", provider=provider_label, override=True)
        _log("tts.synth.start", desired_mime=desired_mime, chunk_bytes=chunk_bytes, override=True)
        total_audio_bytes = len(audio_payload or b"")
        state['started'] = True
        _log(
            "tts.synth.done",
            desired_mime=desired_mime,
            chunk_bytes=chunk_bytes,
            dur_ms=0,
            audio_bytes=total_audio_bytes,
            override=True,
        )

    if not audio_payload:
        state['error'] = 'empty_audio'
        _log("tts.synth.error", error='empty_audio', override=audio_override)
        tracker.error('empty_audio', state=_tts_state_snapshot())
        return False

    tracker.queue()

    completion_event = _prepare_tts_completion_event(session_id, turn_key)

    payload_for_stream = audio_payload

    def _run():
        total_audio_bytes = 0
        chunk_count = 0
        chunk_bytes_sent = 0
        canceled = False
        truncated = False
        utterance_end_sent = False
        max_frames = 256  # guardrail

        def _emit_utterance_end(reason: str) -> None:
            nonlocal utterance_end_sent
            if utterance_end_sent:
                _log("tts.utterance_end.skip", reason=reason, already_sent=True)
                return
            _log("tts.utterance_end.emit", reason=reason, already_sent=utterance_end_sent)
            end_payload = {
                "session_id": session_id,
                "turn_id": safe_turn_id,
                "reason": reason,
            }
            try:
                _admin_emit('UtteranceEnd', **end_payload)
            except Exception:
                pass
            try:
                bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": safe_turn_id})
            except Exception:
                pass
            utterance_end_sent = True

        stream_payload = audio_bytes if audio_override else payload_for_stream
        try:
            # Small pacing delay if requested
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)

            total_audio_bytes = len(stream_payload or b"")

            start_payload = {"session_id": session_id, "turn_id": safe_turn_id}
            try:
                _admin_emit('tts:start', **start_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event('tts:start', start_payload)

            start_frame: Dict[str, Any] = {"type": "TTS_START", "turn_id": safe_turn_id}
            if correlation_user_msg_id:
                start_frame["correlation_user_msg_id"] = correlation_user_msg_id
            try:
                bus.broadcast(session_id, start_frame)
            except Exception:
                pass

            tracker.start()

            # Chunk and broadcast
            mv = memoryview(stream_payload)
            idx = 0
            first_sent = False
            first_chunk_ts: Optional[float] = None
            last_chunk_ts: Optional[float] = None
            prev_chunk_ts: Optional[float] = stream_start_ts
            chunk_items: List[Dict[str, Any]] = []

            while idx < len(mv):
                # stop early if canceled
                try:
                    from ..ws.bus import bus as _bus_ref
                    if _bus_ref.is_canceled(session_id, safe_turn_id):
                        canceled = True
                        _log(
                            "tts.chunks.cancelled",
                            chunk_count=chunk_count,
                            bytes_sent=chunk_bytes_sent,
                            total_bytes=total_audio_bytes,
                        )
                        break
                except Exception:
                    pass

                next_idx = idx + int(chunk_bytes)
                part = bytes(mv[idx: next_idx])
                idx = next_idx
                chunk_len = len(part)

                try:
                    b64 = base64.b64encode(part).decode("ascii")
                    fr = {
                        "type": "assistant_audio",
                        "turn_id": safe_turn_id,
                        "mime": desired_mime,
                        "audio_chunks": [b64],
                        "is_last": (idx >= len(mv)),
                    }
                    if correlation_user_msg_id:
                        fr["correlation_user_msg_id"] = correlation_user_msg_id

                    bus.broadcast(session_id, fr)

                    if not first_sent:
                        first_sent = True
                        state['first_chunk'] = True
                        try:
                            nudges.cancel_idle_timers(
                                session_id,
                                source="assistant_audio_start",
                                mark=False,
                            )
                        except Exception:
                            pass
                        try:
                            _admin_emit('tts:first_chunk', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
                        except Exception:
                            pass
                        _broadcast_admin_ws_event(
                            'tts:first_chunk',
                            {
                                "session_id": session_id,
                                "turn_id": str(turn_id) if turn_id else None,
                            },
                        )

                    chunk_count += 1
                    chunk_bytes_sent += chunk_len
                    chunk_sent_ts = _t.time()
                    base_ts = first_chunk_ts if first_chunk_ts is not None else stream_start_ts
                    if base_ts is None:
                        dt_ms = 0
                    elif first_chunk_ts is None:
                        dt_ms = int(max(0.0, (chunk_sent_ts - base_ts) * 1000))
                    else:
                        prev = prev_chunk_ts if prev_chunk_ts is not None else first_chunk_ts
                        dt_ms = int(max(0.0, (chunk_sent_ts - (prev or chunk_sent_ts)) * 1000))
                    mark: Optional[str] = None
                    if first_chunk_ts is None:
                        mark = "first"
                        first_chunk_ts = chunk_sent_ts
                    is_last = idx >= len(mv)
                    if is_last:
                        if mark:
                            mark = f"{mark},last" if mark != "last" else "last"
                        else:
                            mark = "last"
                    item: Dict[str, Any] = {"dt_ms": dt_ms, "bytes": chunk_len}
                    if mark:
                        item["mark"] = mark
                    chunk_items.append(item)
                    prev_chunk_ts = chunk_sent_ts
                    last_chunk_ts = chunk_sent_ts
                    if chunk_count >= max_frames:
                        # Safety cut: end stream explicitly if we truncated
                        truncated = True
                        _log(
                            "tts.chunks.truncated",
                            chunk_count=chunk_count,
                            bytes_sent=chunk_bytes_sent,
                            total_bytes=total_audio_bytes,
                            max_frames=max_frames,
                        )
                        _emit_utterance_end("truncated_guard")
                        break
                except Exception:
                    # If a single chunk fails, try to continue; we'll still emit UtteranceEnd.
                    pass

            status = "canceled" if canceled else ("truncated" if truncated else "complete")
            _log(
                "tts.chunks.summary",
                chunk_count=chunk_count,
                bytes_sent=chunk_bytes_sent,
                total_bytes=total_audio_bytes,
                status=status,
                max_frames=max_frames,
            )
        finally:
            # Always mark tts done (admin), and always signal UtteranceEnd if we haven't already.
            done_payload = {
                "session_id": session_id,
                "turn_id": str(turn_id) if turn_id else None,
            }
            try:
                _admin_emit('tts:done', **done_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event('tts:done', done_payload)

            _emit_utterance_end("finalizer")
            try:
                note_utterance_end(session_id)
            except Exception:
                pass

            stream_end_ts = _t.time()
            metrics_payload: Dict[str, Any] = {
                "text_chars": len(text or ""),
                "synth_ms": synth_duration_ms,
            }
            if provider_label:
                metrics_payload["voice"] = provider_label
            if first_chunk_ts is not None and stream_start_ts is not None:
                metrics_payload["first_byte_ms"] = int(
                    max(0.0, (first_chunk_ts - stream_start_ts) * 1000)
                )
            if first_chunk_ts is not None and last_chunk_ts is not None:
                metrics_payload["stream_ms"] = int(
                    max(0.0, (last_chunk_ts - first_chunk_ts) * 1000)
                )
            if stream_start_ts is not None:
                metrics_payload["total_ms"] = int(
                    max(0.0, (stream_end_ts - stream_start_ts) * 1000)
                )

            tracker.end(metrics=metrics_payload, chunks=chunk_items)

            state['done'] = True

            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    pass

            if on_post_silence is not None:
                try:
                    nudges.run_after_silence(
                        session_id,
                        on_post_silence,
                        reason=post_silence_origin or "assistant_audio",
                    )
                except Exception:
                    try:
                        on_post_silence()
                    except Exception:
                        pass

            try:
                if completion_event is not None:
                    _signal_tts_completion(session_id, turn_key)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return True


def _update_assistant_turn_state(session_id: str,
                                 frame: Dict[str, Any],
                                 current_turn_id: Optional[str]) -> Optional[str]:
    if not isinstance(frame, dict):
        return current_turn_id
    ftype = frame.get('type')
    turn_id = frame.get('turn_id')
    if ftype in ('assistant_chunk', 'assistant_audio', 'audio_chunk') and turn_id:
        try:
            bus.note_assistant_turn(session_id, turn_id)
        except Exception:
            pass
        return turn_id
    if ftype in ('assistant_end', 'end'):
        try:
            bus.note_assistant_turn(session_id, None)
        except Exception:
            pass
        return None
    return current_turn_id


def _apply_assistant_turn_state(session_id: str, frames: Iterable[Dict[str, Any]]) -> None:
    current_turn_id: Optional[str] = None
    for frame in frames:
        current_turn_id = _update_assistant_turn_state(session_id, frame, current_turn_id)


def schedule_frames(session_id: str,
                    frames: List[Dict],
                    delay_ms: int = 0,
                    correlation_user_msg_id: Optional[str] = None) -> None:
    """
    Optionally schedule frames with a delay (for pacing). Ensures an assistant_end is emitted.
    Non-blocking: runs in a background thread.
    """
    def _run():
        try:
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)
            current_turn_id = None
            for fr in frames:
                try:
                    # Belt-and-suspenders: never let legacy stamps slip through on rebroadcast.
                    if isinstance(fr, dict) and fr.get('type') == 'assistant_chunk' and 'text' in fr:
                        fr['text'] = _scrub_debug_stamps(fr['text'])
                    if correlation_user_msg_id and isinstance(fr, dict) and 'correlation_user_msg_id' not in fr:
                        fr['correlation_user_msg_id'] = correlation_user_msg_id
                    turn_id_for_admin: Optional[str] = None
                    if isinstance(fr, dict):
                        turn_id_for_admin = fr.get('turn_id') or current_turn_id
                    current_turn_id = _update_assistant_turn_state(session_id, fr, current_turn_id)
                    _broadcast_frames(session_id, [fr], turn_id_for_admin)
                except Exception:
                    pass
            # Ensure an assistant_end terminator exists
            if not any(fr.get('type') in ('assistant_end', 'end') for fr in frames):
                end_fr = {'type': 'assistant_end', 'turn_id': frames[-1].get('turn_id') if frames else None}
                if current_turn_id and not end_fr.get('turn_id'):
                    end_fr['turn_id'] = current_turn_id
                if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
                    end_fr['correlation_user_msg_id'] = correlation_user_msg_id
                try:
                    turn_id_for_admin = end_fr.get('turn_id') or current_turn_id
                    _apply_assistant_turn_state(session_id, [end_fr])
                    _broadcast_frames(session_id, [end_fr], turn_id_for_admin)
                except Exception:
                    pass
        finally:
            frames_done_payload = {"session_id": session_id}
            try:
                _admin_emit('schedule_frames_done', **frames_done_payload)
            except Exception:
                pass
            _broadcast_admin_ws_event('schedule_frames_done', frames_done_payload)

    threading.Thread(target=_run, daemon=True).start()


# ---- WS orchestration helpers (centralize greet/user pipelines) ----

def _emit_ws_outage(session_id: str,
                    turn_id: str,
                    *,
                    correlation_user_msg_id: Optional[str] = None) -> List[Dict[str, Any]]:
    frames: List[Dict[str, Any]] = []
    chunk: Dict[str, Any] = {
        'type': 'assistant_chunk',
        'turn_id': turn_id,
        'text': _WS_PIPELINE_MESSAGE,
        'kb_hits': 0,
        'note': _WS_PIPELINE_UNAVAILABLE_NOTE,
    }
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(chunk)
    end_fr: Dict[str, Any] = {'type': 'assistant_end', 'turn_id': turn_id}
    if correlation_user_msg_id:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)
    _broadcast_frames(session_id, frames, turn_id)
    return frames


def _ws_generation_failed(turn_id: Optional[str], frames: List[Dict[str, Any]]) -> bool:
    return turn_id is None and not frames


def run_ws_greet(session_id: str) -> str:
    """
    Produce assistant text for greet, broadcast frames, schedule TTS audio,
    and nudge UI with state+suggestions. Returns turn_id.
    """
    nudges.cancel_idle_timers(session_id, mark=False)
    forced_tid, _idempotent = get_or_create_greet_turn(session_id, force=False, ttl_sec=DEFAULT_TTL_SEC)
    cfg = db.get_config()
    tid, frames = make_assistant_frames(
        "greet",
        session_id,
        meta={"source": "ws_greet", "channel": "ws"},
        force_turn_id=forced_tid,
        cfg=cfg,
    )
    if _ws_generation_failed(tid, frames):
        tid = _allocate_turn_id(forced_tid)
        frames = _emit_ws_outage(session_id, tid)
        outage_payload = {"session_id": session_id, "phase": 'greet'}
        try:
            _admin_emit('ws_pipeline_outage', **outage_payload)
        except Exception:
            pass
        _broadcast_admin_ws_event('ws_pipeline_outage', outage_payload)
        try:
            bus.broadcast(session_id, {"type": "state", "phase": "ready"})
        except Exception:
            pass
        return tid
    existing_frame_types = [
        fr.get("type")
        for fr in frames
        if isinstance(fr, dict) and fr.get("type") is not None
    ]
    try:
        _apply_assistant_turn_state(session_id, frames)
    except Exception:
        pass
    state_already_sent = any(ft == "state" for ft in existing_frame_types)
    suggestions_already_sent = any(ft == "suggestions" for ft in existing_frame_types)

    ready_sent = state_already_sent
    idle_armed = False

    def _emit_ready_once() -> None:
        nonlocal ready_sent
        if ready_sent:
            return
        try:
            if should_emit_phase(session_id, "ready"):
                set_phase(session_id, "ready", emitted=True)
                bus.broadcast(session_id, {"type": "state", "phase": "ready"})
            else:
                set_phase(session_id, "ready")
            ready_sent = True
        except Exception:
            pass

    def _arm_idle_once() -> None:
        nonlocal idle_armed
        if idle_armed:
            return
        idle_armed = True
        _emit_ready_once()
        nudges.arm_idle_timers(session_id, reason="greet_idle")

    audio_scheduled = False

    # TTS: use first assistant_chunk text if present (feature_audio gating inside schedule_tts_audio)
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            safe_text = _scrub_debug_stamps(text_for_tts)
            try:
                _jlog(
                    "tts.pre_schedule",
                    session_id=session_id,
                    sid=session_id,
                    turn_id=tid,
                    flow="greet",
                    **_tts_text_markers(safe_text),
                )
            except Exception:
                pass
            audio_scheduled = schedule_tts_audio(
                session_id,
                safe_text,
                turn_id=tid,
                on_complete=_arm_idle_once,
                flow_phase="greet",
                flow_include_turn_id=False,
            )
            if not audio_scheduled:
                try:
                    _jlog(
                        "tts.greet_audio_skipped",
                        session_id=session_id,
                        sid=session_id,
                        turn_id=tid,
                        flow="greet",
                        reason="schedule_failed",
                        **_tts_text_markers(safe_text),
                    )
                except Exception:
                    pass
                _arm_idle_once()
    except Exception:
        pass

    # UI nudges (only emit if the assistant frames didn't already do so)
    try:
        if not audio_scheduled:
            _arm_idle_once()
        if not suggestions_already_sent:
            greet_legacy = hygienic_suggestions("")
            base_suggestions = merge_suggestions(greet_legacy)
            _log_suggestions_made(
                turn_id=tid,
                policy_chips=[],
                legacy_suggestions=greet_legacy,
                merged_suggestions=base_suggestions,
                cfg=cfg,
                session_id=session_id,
            )
            if base_suggestions:
                bus.broadcast(
                    session_id,
                    {"type": "suggestions", "turn_id": tid, "items": build_suggestion_items(base_suggestions)},
                )
    except Exception:
        pass
    return tid


def run_ws_user_turn(session_id: str,
                     text: str,
                     correlation_user_msg_id: Optional[str] = None,
                     *,
                     meta_overrides: Optional[Dict[str, Any]] = None) -> str:
    """
    Produce assistant text for a user turn and schedule both text pacing and TTS.
    Mirrors HTTP /api_v1/chat behavior for consistency.
    """
    nudges.cancel_idle_timers(session_id, mark=False)
    meta = {"source": "user_ws", "channel": "ws"}
    if isinstance(meta_overrides, dict):
        for key, value in meta_overrides.items():
            try:
                meta[key] = copy.deepcopy(value)
            except Exception:
                meta[key] = value

    tid, frames = make_assistant_frames(text, session_id, meta=meta,
                                        correlation_user_msg_id=correlation_user_msg_id)
    if _ws_generation_failed(tid, frames):
        tid = _allocate_turn_id(force_turn_id=None)
        frames = _emit_ws_outage(session_id, tid, correlation_user_msg_id=correlation_user_msg_id)
        try:
            _apply_assistant_turn_state(session_id, frames)
        except Exception:
            pass
        outage_payload = {"session_id": session_id, "phase": 'turn'}
        try:
            _admin_emit('ws_pipeline_outage', **outage_payload)
        except Exception:
            pass
        _broadcast_admin_ws_event('ws_pipeline_outage', outage_payload)
        return tid
    try:
        _apply_assistant_turn_state(session_id, frames)
    except Exception:
        pass
    existing_frame_types = [
        fr.get("type")
        for fr in frames
        if isinstance(fr, dict) and fr.get("type") is not None
    ]
    ready_sent = any(ft == "state" for ft in existing_frame_types)

    def _emit_ready_once() -> None:
        nonlocal ready_sent
        if ready_sent:
            return
        try:
            if should_emit_phase(session_id, "ready"):
                set_phase(session_id, "ready", emitted=True)
                bus.broadcast(session_id, {"type": "state", "phase": "ready"})
            else:
                set_phase(session_id, "ready")
            ready_sent = True
        except Exception:
            pass

    def _arm_idle_once() -> None:
        _emit_ready_once()
        nudges.arm_idle_timers(session_id, reason="assistant_idle")

    audio_scheduled = False

    # TTS for assistant text
    try:
        first_chunk_frame = next((fr for fr in frames if fr.get("type") == "assistant_chunk"), None)
        text_for_tts = first_chunk_frame.get("text") if isinstance(first_chunk_frame, dict) else ""
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            safe_text = _scrub_debug_stamps(text_for_tts)
            try:
                payload = {
                    "session_id": session_id,
                    "sid": session_id,
                    "turn_id": tid,
                    "flow": "user",
                    **_tts_text_markers(safe_text),
                }
                if correlation_user_msg_id:
                    payload["correlation_user_msg_id"] = correlation_user_msg_id
                _jlog("tts.pre_schedule", **payload)
            except Exception:
                pass
            clarifier_band = ""
            teacher_move = ""
            clarify_variant = ""
            if isinstance(first_chunk_frame, dict):
                clarifier_band = str(
                    first_chunk_frame.get("planner_confidence_band")
                    or first_chunk_frame.get("policy_confidence_band")
                    or ""
                ).strip().lower()
                teacher_move = str(first_chunk_frame.get("teacher_move") or "").strip().lower()
                clarify_variant = str(first_chunk_frame.get("clarify_variant") or "").strip().lower()
            is_clarifier_audio = clarifier_band in {"medium", "low"} and (
                bool(clarify_variant) or teacher_move == "clarify"
            )
            schedule_kwargs: Dict[str, Any] = {
                "turn_id": tid,
                "correlation_user_msg_id": correlation_user_msg_id,
            }
            if is_clarifier_audio:
                schedule_kwargs["chunk_bytes"] = 4096
                schedule_kwargs["on_post_silence"] = _arm_idle_once
                schedule_kwargs["post_silence_origin"] = "clarifier_idle"
            else:
                schedule_kwargs["on_complete"] = _arm_idle_once
            audio_scheduled = schedule_tts_audio(
                session_id,
                safe_text,
                **schedule_kwargs,
            )
    except Exception:
        pass
    if not audio_scheduled:
        try:
            _arm_idle_once()
        except Exception:
            try:
                _emit_ready_once()
            except Exception:
                pass
    return tid


def emit_nlu_for_e2e(nlu: dict, session_goal: dict):
    try:
        from app.api_v1.admin import _emit as _admin_emit
        payload = dict(nlu or {})
        payload.setdefault('entities', payload.get('entities') or {})
        _admin_emit('nlu', **payload)
        if session_goal:
            _admin_emit('session_goal', **session_goal)
    except Exception:
        pass
