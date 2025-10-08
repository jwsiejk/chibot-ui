"""Lightweight universal interpretation for user turns.

The goal of this module is to normalize every user turn into a common
set of dialog "knobs" that downstream components can rely on, regardless of
whether an LLM-based interpreter is available.  The heuristics below favor
cheap text based signals so that the interpreter can run on every turn.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

# Canonical field defaults – all consumers can rely on these keys existing.
_DEFAULT_RESULT: Dict[str, Any] = {
    "user_goal": "seek_information",
    "phase": "discover",
    "depth": "normal",
    "delivery_pref": "explain",
    "intent_hint": None,
    "entities": {"products": [], "keywords": []},
    "urgency": "normal",
    "multi_intent": False,
    "confidence": 0.0,
    "needs_clarification": False,
    "out_of_domain": False,
    "missing": [],
}

_QUESTION_WORDS = {"who", "what", "where", "when", "why", "how"}
_URGENCY_KEYWORDS = {"urgent", "asap", "immediately", "right now", "critical", "priority", "emergency"}
_LOW_URGENCY_KEYWORDS = {"whenever", "no rush", "sometime", "when you can"}
_STEP_KEYWORDS = {"step", "walk me", "walk-through", "walkthrough", "guide me", "instructions", "setup"}
_SUMMARY_KEYWORDS = {"quick", "tl;dr", "summary", "brief", "short", "overview"}
_DEEP_KEYWORDS = {"detailed", "deep", "in depth", "comprehensive", "full"}
_LIST_KEYWORDS = {"list", "options", "compare", "versus", "vs", "choices"}
_OUT_OF_DOMAIN_KEYWORDS = {"joke", "weather", "stock", "stocks", "movie", "music", "song", "sports", "game score"}
_MAYBE_MULTI_SEPARATORS = (" and ", " also ", " plus ", " besides ", " as well as ", " / ", ";")


def _baseline_result() -> Dict[str, Any]:
    """Return a deep copy of the canonical default payload."""

    entities = _DEFAULT_RESULT["entities"]
    return {
        "user_goal": _DEFAULT_RESULT["user_goal"],
        "phase": _DEFAULT_RESULT["phase"],
        "depth": _DEFAULT_RESULT["depth"],
        "delivery_pref": _DEFAULT_RESULT["delivery_pref"],
        "intent_hint": _DEFAULT_RESULT["intent_hint"],
        "entities": {"products": list(entities.get("products", [])), "keywords": list(entities.get("keywords", []))},
        "urgency": _DEFAULT_RESULT["urgency"],
        "multi_intent": _DEFAULT_RESULT["multi_intent"],
        "confidence": _DEFAULT_RESULT["confidence"],
        "needs_clarification": _DEFAULT_RESULT["needs_clarification"],
        "out_of_domain": _DEFAULT_RESULT["out_of_domain"],
        "missing": list(_DEFAULT_RESULT["missing"]),
    }


def _ensure_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value is None:
        return []
    return [value]


def _normalize_entities(raw_entities: Any, products: Iterable[str]) -> Dict[str, List[str]]:
    products_list = [p for p in _ensure_iterable(products) if isinstance(p, str)]
    keywords: List[str] = []
    if isinstance(raw_entities, Mapping):
        for key, val in raw_entities.items():
            if isinstance(val, str):
                keywords.append(val)
            elif isinstance(val, Iterable) and not isinstance(val, (str, bytes)):
                for item in val:
                    if isinstance(item, str):
                        keywords.append(item)
            elif val is not None:
                keywords.append(str(val))
    elif isinstance(raw_entities, Iterable) and not isinstance(raw_entities, (str, bytes)):
        for item in raw_entities:
            if isinstance(item, str):
                keywords.append(item)
            elif isinstance(item, Mapping):
                for val in item.values():
                    if isinstance(val, str):
                        keywords.append(val)

    # Deduplicate while preserving order
    def _dedupe(items: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            lowered = item.strip()
            if not lowered:
                continue
            lowered_key = lowered.lower()
            if lowered_key in seen:
                continue
            seen.add(lowered_key)
            out.append(lowered)
        return out

    return {
        "products": _dedupe(products_list),
        "keywords": _dedupe(keywords),
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w/+]+", text))


def _sentence_count(text: str) -> int:
    segments = re.split(r"[.!?]+", text)
    return len([seg for seg in segments if seg.strip()])


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in needles)


def _clamp_conf(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(0.0, min(1.0, round(value, 4)))


def _infer_user_goal(intent: Optional[str]) -> str:
    mapping = {
        "greet": "greeting",
        "how_to_steps": "complete_task",
        "troubleshoot": "resolve_issue",
        "compare_options": "compare_options",
        "sizing_licensing": "plan_capacity",
    }
    if intent in mapping:
        return mapping[intent]
    return _DEFAULT_RESULT["user_goal"]


def _infer_phase(intent: Optional[str], needs_clarification: bool) -> str:
    if needs_clarification:
        return "clarify"
    mapping = {
        "greet": "opening",
        "how_to_steps": "execute",
        "troubleshoot": "diagnose",
        "compare_options": "evaluate",
        "sizing_licensing": "plan",
    }
    return mapping.get(intent, _DEFAULT_RESULT["phase"])


def _infer_delivery_pref(intent: Optional[str], text: str) -> str:
    lowered = text.lower()
    if intent == "how_to_steps" or _contains_any(lowered, _STEP_KEYWORDS):
        return "steps"
    if _contains_any(lowered, _SUMMARY_KEYWORDS):
        return "summary"
    if intent == "compare_options" or _contains_any(lowered, _LIST_KEYWORDS):
        return "list"
    return _DEFAULT_RESULT["delivery_pref"]


def _infer_depth(intent: Optional[str], expected_depth: Optional[str], text: str) -> str:
    lowered = text.lower()
    if expected_depth in {"brief", "normal", "medium"}:
        if expected_depth == "medium":
            return "normal"
        return expected_depth
    if _contains_any(lowered, _SUMMARY_KEYWORDS):
        return "brief"
    if _contains_any(lowered, _DEEP_KEYWORDS) or _word_count(lowered) > 35:
        return "deep"
    if intent == "troubleshoot" and _word_count(lowered) >= 20:
        return "deep"
    return _DEFAULT_RESULT["depth"]


def _detect_multi_intent(text: str) -> bool:
    lowered = text.lower()
    if text.count("?") >= 2:
        return True
    sentence_count = _sentence_count(text)
    if sentence_count >= 2 and any(sep in lowered for sep in _MAYBE_MULTI_SEPARATORS):
        return True
    return False


def _detect_urgency(text: str, meta: Mapping[str, Any]) -> str:
    lowered = text.lower()
    if _contains_any(lowered, _URGENCY_KEYWORDS):
        return "high"
    tags = meta.get("tags") if isinstance(meta, Mapping) else None
    if isinstance(tags, Mapping) and tags.get("in_a_hurry"):
        return "high"
    if _contains_any(lowered, _LOW_URGENCY_KEYWORDS):
        return "low"
    return "normal"


def _detect_out_of_domain(text: str, intent: Optional[str]) -> bool:
    if intent and intent in {"out_of_domain", "oob", "smalltalk"}:
        return True
    lowered = text.lower()
    return _contains_any(lowered, _OUT_OF_DOMAIN_KEYWORDS)


def ensure_all_fields(candidate: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a canonical result, filling any missing keys with defaults."""

    base = _baseline_result()
    if not isinstance(candidate, Mapping):
        return base
    for key in list(base.keys()):
        if key in candidate:
            base[key] = candidate[key]
    # Ensure nested dicts are cloned and well formed
    entities = base.get("entities")
    if not isinstance(entities, MutableMapping):
        entities = {"products": [], "keywords": []}
    else:
        entities = {
            "products": list(_ensure_iterable(entities.get("products"))),
            "keywords": list(_ensure_iterable(entities.get("keywords"))),
        }
    base["entities"] = entities
    missing = base.get("missing")
    if not isinstance(missing, list):
        missing = list(_ensure_iterable(missing))
    base["missing"] = missing
    base["multi_intent"] = bool(base.get("multi_intent"))
    base["needs_clarification"] = bool(base.get("needs_clarification"))
    base["out_of_domain"] = bool(base.get("out_of_domain"))
    try:
        base["confidence"] = _clamp_conf(float(base.get("confidence", 0.0)))
    except Exception:
        base["confidence"] = 0.0
    return base


def interpret(text: str,
              *,
              meta: Optional[Mapping[str, Any]] = None,
              dialog_nlu: Optional[Mapping[str, Any]] = None,
              config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return canonical interpretation knobs for the provided user text."""

    text = (text or "").strip()
    meta = meta or {}
    if isinstance(dialog_nlu, Mapping):
        nlu = dict(dialog_nlu)
    else:
        nlu = {}
        if isinstance(meta, Mapping):
            candidate = meta.get("dialog_nlu") or meta.get("nlu")
            if isinstance(candidate, Mapping):
                nlu = dict(candidate)

    result = _baseline_result()

    if not text:
        result["needs_clarification"] = True
        result["missing"].append("message")
        return result

    lowered = text.lower()
    word_count = _word_count(lowered)
    intent = nlu.get("intent") if isinstance(nlu, Mapping) else None
    expected_depth = None
    if isinstance(nlu, Mapping):
        expected_depth = nlu.get("expected_depth")

    result["intent_hint"] = intent
    result["user_goal"] = _infer_user_goal(intent)
    products = nlu.get("products") if isinstance(nlu, Mapping) else []
    entities = nlu.get("entities") if isinstance(nlu, Mapping) else {}
    result["entities"] = _normalize_entities(entities, products)

    # Determine base confidence
    if isinstance(nlu, Mapping) and isinstance(nlu.get("confidence"), (int, float)):
        base_conf = float(nlu["confidence"])
    else:
        base_conf = 0.35 if text else 0.0
        if word_count > 4:
            base_conf += min(0.25, word_count * 0.015)
        if text.endswith("?"):
            base_conf += 0.05
    base_conf = _clamp_conf(base_conf)

    needs_clarification = False
    if isinstance(nlu, Mapping) and nlu.get("needs_scoping"):
        needs_clarification = True
    elif word_count <= 3:
        needs_clarification = True
    elif text.endswith("?") and not _contains_any(lowered, {"details", "more info", "explain"}) and word_count < 6:
        needs_clarification = True

    if needs_clarification:
        base_conf = _clamp_conf(base_conf * 0.7)

    out_of_domain = _detect_out_of_domain(lowered, intent)
    if out_of_domain:
        base_conf = _clamp_conf(base_conf * 0.6)

    result["confidence"] = base_conf
    result["needs_clarification"] = needs_clarification
    result["out_of_domain"] = out_of_domain
    result["phase"] = _infer_phase(intent, needs_clarification)
    result["delivery_pref"] = _infer_delivery_pref(intent, text)
    result["depth"] = _infer_depth(intent, expected_depth, text)
    result["urgency"] = _detect_urgency(text, meta)
    result["multi_intent"] = _detect_multi_intent(text)

    missing: List[str] = []
    if needs_clarification:
        missing.append("details")
    if intent in {"how_to_steps", "troubleshoot", "sizing_licensing"} and not result["entities"].get("products"):
        missing.append("product")
    if intent == "troubleshoot" and word_count < 8:
        missing.append("issue_detail")
    if not intent:
        missing.append("intent")

    result["missing"] = missing

    # Augment keyword entities with question words for extra context
    if any(lowered.startswith(q + " ") for q in _QUESTION_WORDS):
        result["entities"].setdefault("keywords", [])
        if lowered.split(" ", 1)[0] not in result["entities"]["keywords"]:
            result["entities"]["keywords"].append(lowered.split(" ", 1)[0])

    return ensure_all_fields(result)


__all__ = ["interpret", "ensure_all_fields"]

