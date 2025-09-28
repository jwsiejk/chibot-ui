# app/services/streaming.py — Production-grade assistant framing
# Guarantees assistant_* frames are broadcast even if the LLM provider fails.
# Text-first design: generate/broadcast assistant text; TTS is optional elsewhere.

from __future__ import annotations
from typing import List, Dict, Tuple, Optional, Any, Iterable
import base64
import threading, time as _t
import os
import re

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
from ..obs import jlog as _jlog
from ..personas.store import PersonaManager, PersonaStore
from ..personas.prompt_builder import build_messages
from .. import nlu as _nlu
from ..nlu.classifier import classify as classify_turn
from ..dialog.policy import pick as pick_dialog_policy
from ..config import load_settings

try:
    from ..api_v1.admin import _emit as _admin_emit  # SSE to Admin
except Exception:
    def _admin_emit(*a, **k):  # no-op if admin channel absent
        pass


def _emit_turn_action_metadata(turn_id: Optional[str],
                               meta: Optional[Dict[str, Any]],
                               *,
                               is_greet: bool) -> None:
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
    payload = {
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
    try:
        _admin_emit("turn_action_metadata", **payload)
    except Exception:
        pass


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


def _short_greeting(text: str) -> str:
    try:
        candidate = _collapse_whitespace(text or "Hi there!")
        if not candidate:
            return "Hi there!"
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


def _should_use_foundation(seed_text: str) -> bool:
    if not ENABLE_CHIP_FOUNDATION:
        return False
    try:
        normalized = str(seed_text or "").strip().lower()
    except Exception:
        return False
    if not normalized:
        return False
    return True


_POLICY_SUGGESTION_ACTIONS = frozenset({"ask_clarify", "offer_steps"})

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
    return normalized or default


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
    if topic:
        if verbosity == "brief":
            return f"What part of {topic} should we focus on?"
        return f"What part of {topic} should we focus on so I can help?"
    if verbosity == "brief":
        return "What should we clarify so I can help?"
    return "What detail should we clarify so I can point you the right way?"


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
    if normalized == "give_brief_answer":
        return _format_brief_answer(text, meta)
    if normalized == "offer_steps":
        return _format_offer_steps(text, meta)
    if normalized == "summarize_next_actions":
        return _format_next_actions(text, meta)
    return text


def _call_foundation_llm(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    client = make_openai_client()
    model = (
        cfg.get("openai_model")
        or cfg.get("OPENAI_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )
    temperature = float(cfg.get("gen_temperature", 0.3))
    top_p = float(cfg.get("gen_top_p", 1.0))
    payload = [dict(m) for m in messages]
    resp = client.chat.completions.create(
        model=model,
        messages=payload,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )
    return (resp.choices[0].message.content or "").strip()


def _call_foundation_with_retry(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    primary = _call_foundation_llm(messages, cfg)
    human = humanize_text(primary)
    if not human:
        return human
    if not sounds_botty(human):
        return human
    try:
        strict_msgs = _apply_system_shim(messages, STRICT_SYSTEM_SHIM)
        strict_reply = _call_foundation_llm(strict_msgs, cfg)
        strict_human = humanize_text(strict_reply)
        if strict_human:
            return strict_human
    except Exception:
        pass
    return human


def _broadcast_frames(session_id: str, frames: List[Dict], turn_id: str) -> None:
    try:
        for fr in frames:
            bus.broadcast(session_id, fr)
            if fr.get('type') == 'assistant_chunk':
                _admin_emit('assistant_chunk', session_id=session_id, turn_id=str(turn_id))
            elif fr.get('type') == 'assistant_end':
                _admin_emit('assistant_end', session_id=session_id, turn_id=str(turn_id))
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


def prepare_turn_metadata(seed_text: str,
                          meta: Optional[Dict[str, Any]] = None,
                          *,
                          cfg: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Ensure dialog metadata is populated for downstream consumers."""
    cfg = cfg or db.get_config()
    incoming_meta = meta if isinstance(meta, dict) else {}
    target_meta = incoming_meta if incoming_meta is meta and isinstance(meta, dict) else dict(incoming_meta)

    labels = classify(seed_text, target_meta)
    dialog_nlu = dict(labels)

    raw_policy = pick_dialog_policy(dialog_nlu) or {}
    policy = dict(raw_policy)
    if policy.get("action") and "teacher_move" not in policy:
        policy["teacher_move"] = policy["action"]

    if not isinstance(target_meta.get("nlu"), dict):
        target_meta["nlu"] = dict(dialog_nlu)
    else:
        try:
            target_meta["nlu"].update(dialog_nlu)
        except Exception:
            target_meta["nlu"] = dict(dialog_nlu)
    target_meta[_DIALOG_NLU_KEY] = dict(dialog_nlu)

    action = target_meta.get(_DIALOG_ACTION_KEY) or target_meta.get("action")
    if not action:
        action = policy.get("action") or policy.get("teacher_move") or "respond"
    target_meta["action"] = action
    target_meta[_DIALOG_ACTION_KEY] = action

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
                          force_turn_id: Optional[str] = None) -> Tuple[Optional[str], List[Dict]]:
    """
    Produce assistant frames for a given user seed text using the configured LLM provider.
    ALWAYS returns a frames list that includes at least:
        - one 'assistant_chunk' (with either model text OR a safe fallback), and
        - one 'assistant_end'.
    Also broadcasts frames to the WS bus as they are prepared.
    """
    cfg = db.get_config()
    meta, classified_labels, classified_policy = prepare_turn_metadata(seed_text, meta, cfg=cfg)
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
                force_turn_id=force_turn_id,
                is_greet=is_greet,
                fallback_line=fallback_line,
                fallback_on_empty=fallback_on_empty,
                fallback_on_error=fallback_on_error,
                fallback_emit_event=fallback_emit_event,
                action=action,
            )
        except Exception as e:
            try:
                _admin_emit('foundation_pipeline_error', error=e.__class__.__name__)
            except Exception:
                pass

    if skip_legacy:
        return None, []

    return _make_legacy_frames(
        seed_text,
        session_id,
        meta,
        cfg,
        correlation_user_msg_id=correlation_user_msg_id,
        force_turn_id=force_turn_id,
        is_greet=is_greet,
        fallback_line=fallback_line,
        fallback_on_empty=fallback_on_empty,
        fallback_on_error=fallback_on_error,
        fallback_emit_event=fallback_emit_event,
        action=action,
        labels=classified_labels,
        policy=classified_policy,
    )


def _make_foundation_frames(seed_text: str,
                            session_id: str,
                            meta: Dict[str, Any],
                            cfg: Dict[str, Any],
                            *,
                            correlation_user_msg_id: Optional[str],
                            force_turn_id: Optional[str],
                            is_greet: bool,
                            fallback_line: str,
                            fallback_on_empty: bool,
                            fallback_on_error: bool,
                            fallback_emit_event: bool,
                            action: Optional[str] = None) -> Tuple[str, List[Dict]]:
    store = PersonaStore()
    persona = PersonaManager(store).get_active()
    dialog_meta = dict(meta or {})

    nlu_result = _nlu.infer(seed_text, persona['id'], dialog_meta, store)
    policy = _nlu.policy.decide(nlu_result, nlu_result.get('tags', {}), persona['id'], store) or {}

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
    messages, teacher_move, prompt_hash = build_messages(
        persona=persona,
        user_text=seed_text,
        dialog_meta=prompt_meta,
        examples=examples,
    )

    use_llm = normalized_action != "ask_clarify"
    if use_llm:
        reply = _call_foundation_with_retry(messages, cfg)
    else:
        reply = _build_clarify_question(seed_text, meta)

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

    turn_id = _allocate_turn_id(force_turn_id)

    if fallback_fired and fallback_emit_event:
        try:
            _admin_emit('fallback', session_id=session_id, turn_id=turn_id, reason=fallback_reason or 'unknown')
        except Exception:
            pass

    _emit_turn_action_metadata(turn_id, meta, is_greet=is_greet)

    frames: List[Dict] = []
    active_teacher_move = policy_action or teacher_move
    chunk: Dict[str, Any] = {
        'type': 'assistant_chunk',
        'turn_id': turn_id,
        'text': safe_reply,
        'kb_hits': 0,
        'intent': nlu_result.get('intent'),
        'teacher_move': active_teacher_move,
    }
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(chunk)

    frames.append({'type': 'state', 'phase': 'ready'})

    legacy_suggestions = []
    try:
        if cfg.get('suggestions_enabled', True):
            legacy_suggestions = hygienic_suggestions(safe_reply)
    except Exception:
        legacy_suggestions = []
    merged_suggestions = merge_suggestions(policy_chips, legacy_suggestions)
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

    _broadcast_frames(session_id, frames, turn_id)
    return turn_id, frames


def _make_legacy_frames(seed_text: str,
                        session_id: str,
                        meta: Dict[str, Any],
                        cfg: Dict[str, Any],
                        *,
                        correlation_user_msg_id: Optional[str],
                        force_turn_id: Optional[str],
                        is_greet: bool,
                        fallback_line: str,
                        fallback_on_empty: bool,
                        fallback_on_error: bool,
                        fallback_emit_event: bool,
                        action: Optional[str] = None,
                        labels: Optional[Dict[str, Any]] = None,
                        policy: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict]]:
    persona = _get_persona_for_session(session_id)

    if labels is None:
        labels = classify(seed_text, meta)
    else:
        labels = dict(labels)
    if policy is None:
        policy = pick_dialog_policy(labels) or {}
    else:
        policy = dict(policy or {})

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

    reply: str
    error_note: Optional[str] = None
    use_llm = normalized_action != "ask_clarify"

    if use_llm:
        try:
            provider = get_provider(cfg)
        except Exception as e:
            provider = None
            error_note = "llm_not_available"
            _admin_emit("llm_provider_error", error=e.__class__.__name__)
        if provider is not None:
            try:
                reply = provider.generate_reply(prompt, persona=persona, teacher_move=teacher_move, context={'kb': kb})
            except Exception as e:
                error_note = f"llm_error:{e.__class__.__name__}"
                reply = _LEGACY_WARMUP_LINE
                _admin_emit("llm_generate_error", error=e.__class__.__name__)
        else:
            reply = "Hi! I’m ready to help."
    else:
        reply = _build_clarify_question(seed_text, meta)

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

    turn_id = _allocate_turn_id(force_turn_id)

    if fallback_fired and fallback_emit_event:
        try:
            _admin_emit('fallback', session_id=session_id, turn_id=turn_id, reason=fallback_reason or 'unknown')
        except Exception:
            pass

    _emit_turn_action_metadata(turn_id, meta, is_greet=is_greet)

    frames: List[Dict] = []

    active_teacher_move = teacher_move
    chunk = {
        'type': 'assistant_chunk',
        'turn_id': turn_id,
        'text': safe_reply,
        'kb_hits': len(kb),
    }
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
def schedule_tts_audio(session_id: str,
                       text: str,
                       turn_id: str | None = None,
                       correlation_user_msg_id: Optional[str] = None,
                       chunk_bytes: int = 8192,
                       delay_ms: int = 0) -> None:
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
    Non-blocking: runs in a background thread.
    """
    if not text:
        return
    cfg = db.get_config()
    feature_audio = bool(cfg.get("feature_audio", True))

    # Track TTS lifecycle for admin truth
    tts_tbl = db.memory.setdefault('tts_status', {})
    sess_tbl = tts_tbl.setdefault(session_id, {})
    turn_key = str(turn_id) if turn_id is not None else 'greet'
    state = sess_tbl.setdefault(turn_key, {'started': False, 'first_chunk': False, 'done': False, 'error': None})

    if not feature_audio:
        return

    def _run():
        try:
            # Small pacing delay if requested
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)

            # Pick provider (vendor only)
            from app.services.tts_provider import get_tts_provider
            provider = get_tts_provider(cfg or {})

            # Determine desired MIME from configured output format
            desired_mime = _guess_tts_mime(cfg.get('tts_output_format'))

            # Synthesize
            try:
                # Re-scrub here as a safety net in case any upstream callers missed it
                audio_bytes, _vis = provider.synth(_scrub_debug_stamps(text))
                state['started'] = True
                try:
                    _admin_emit('tts:start', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
                except Exception:
                    pass
            except Exception as e:
                state['error'] = str(e)
                try:
                    _admin_emit('tts:error', session_id=session_id, turn_id=str(turn_id) if turn_id else None, error=str(e))
                except Exception:
                    pass
                # Even on error, signal end-of-utterance so clients can clean up gracefully
                try:
                    bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
                except Exception:
                    pass
                return

            # Chunk and broadcast
            mv = memoryview(audio_bytes)
            idx = 0
            max_frames = 256  # guardrail
            sent = 0
            first_sent = False

            while idx < len(mv):
                # stop early if canceled
                try:
                    from ..ws.bus import bus as _bus_ref
                    if _bus_ref.is_canceled(session_id, str(turn_id) if turn_id else None):
                        break
                except Exception:
                    pass

                next_idx = idx + int(chunk_bytes)
                part = bytes(mv[idx: next_idx])
                idx = next_idx

                try:
                    b64 = base64.b64encode(part).decode("ascii")
                    fr = {
                        "type": "assistant_audio",
                        "turn_id": str(turn_id) if turn_id else None,
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
                            _admin_emit('tts:first_chunk', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
                        except Exception:
                            pass

                    sent += 1
                    if sent >= max_frames:
                        # Safety cut: end stream explicitly if we truncated
                        try:
                            bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
                        except Exception:
                            pass
                        break
                except Exception:
                    # If a single chunk fails, try to continue; we'll still emit UtteranceEnd.
                    pass
        finally:
            # Always mark tts done (admin), and always signal UtteranceEnd if we haven't already.
            try:
                _admin_emit('tts:done', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
            except Exception:
                pass

            try:
                bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
            except Exception:
                pass

            state['done'] = True

    threading.Thread(target=_run, daemon=True).start()


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
                    current_turn_id = _update_assistant_turn_state(session_id, fr, current_turn_id)
                    bus.broadcast(session_id, fr)
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
                    _apply_assistant_turn_state(session_id, [end_fr])
                    bus.broadcast(session_id, end_fr)
                except Exception:
                    pass
        finally:
            try:
                _admin_emit('schedule_frames_done', session_id=session_id)
            except Exception:
                pass

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
    forced_tid, _idempotent = get_or_create_greet_turn(session_id, force=False, ttl_sec=DEFAULT_TTL_SEC)
    tid, frames = make_assistant_frames(
        "greet",
        session_id,
        meta={"source": "ws_greet"},
        force_turn_id=forced_tid,
    )
    if _ws_generation_failed(tid, frames):
        tid = _allocate_turn_id(forced_tid)
        frames = _emit_ws_outage(session_id, tid)
        try:
            _admin_emit('ws_pipeline_outage', session_id=session_id, phase='greet')
        except Exception:
            pass
        try:
            bus.broadcast(session_id, {"type": "state", "phase": "ready"})
        except Exception:
            pass
        return tid
    # TTS: use first assistant_chunk text if present (feature_audio gating inside schedule_tts_audio)
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid)
    except Exception:
        pass
    # UI nudges
    try:
        bus.broadcast(session_id, {"type": "state", "phase": "ready"})
        base_suggestions = merge_suggestions(hygienic_suggestions(""))
        if base_suggestions:
            bus.broadcast(
                session_id,
                {"type": "suggestions", "turn_id": tid, "items": build_suggestion_items(base_suggestions)},
            )
    except Exception:
        pass
    return tid


def run_ws_user_turn(session_id: str, text: str, correlation_user_msg_id: Optional[str] = None) -> str:
    """
    Produce assistant text for a user turn and schedule both text pacing and TTS.
    Mirrors HTTP /api_v1/chat behavior for consistency.
    """
    base_meta = {"source": "user_ws"}
    nlu_result = classify_turn(text, meta=base_meta)
    policy = pick_dialog_policy(nlu_result)
    meta = {
        **base_meta,
        "nlu": nlu_result,
        "action": policy["action"],
        "verbosity": policy["verbosity"],
        "show_suggestions": policy["show_suggestions"],
    }

    tid, frames = make_assistant_frames(text, session_id, meta=meta,
                                        correlation_user_msg_id=correlation_user_msg_id)
    if _ws_generation_failed(tid, frames):
        tid = _allocate_turn_id(force_turn_id=None)
        frames = _emit_ws_outage(session_id, tid, correlation_user_msg_id=correlation_user_msg_id)
        try:
            _apply_assistant_turn_state(session_id, frames)
        except Exception:
            pass
        try:
            _admin_emit('ws_pipeline_outage', session_id=session_id, phase='turn')
        except Exception:
            pass
        return tid
    try:
        _apply_assistant_turn_state(session_id, frames)
    except Exception:
        pass
    # TTS for assistant text
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    return tid
