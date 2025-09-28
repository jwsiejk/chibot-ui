"""Heuristic NLU classifier for Pure Storage assistant flows."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Tuple

_LEXICON_FILENAME = "products_lexicon.json"
_WORD_RE = re.compile(r"[\w/+]+")


@lru_cache(maxsize=1)
def _load_lexicon() -> List[Tuple[str, Tuple[str, ...]]]:
    """Return the product lexicon as a list of (canonical, aliases) tuples."""
    path = os.path.join(os.path.dirname(__file__), _LEXICON_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return []

    compiled: List[Tuple[str, Tuple[str, ...]]] = []
    if isinstance(raw, dict):
        for canonical, aliases in raw.items():
            if not canonical:
                continue
            normalized: List[str] = []

            def _push(term: str) -> None:
                term = (term or "").strip()
                if not term:
                    return
                lowered = term.lower()
                if lowered not in normalized:
                    normalized.append(lowered)

            _push(canonical)
            if isinstance(aliases, Iterable):
                for alias in aliases:
                    if isinstance(alias, str):
                        _push(alias)
            compiled.append((canonical, tuple(normalized)))
    return compiled


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    alias = alias.strip().lower()
    if not alias:
        return False
    if len(alias) <= 3:
        return bool(re.search(rf"\b{re.escape(alias)}\b", text))
    return alias in text


def _detect_products(text: str) -> List[str]:
    lowered = text.lower()
    matches: List[str] = []
    for canonical, aliases in _load_lexicon():
        if any(_contains_alias(lowered, alias) for alias in aliases):
            matches.append(canonical)
    return matches


def _has_any(text: str, needles: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in needles)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


def _classify_intent(text: str) -> Tuple[str, float]:
    lowered = text.lower()
    score = 0.0
    intent = "broad_topic_help"

    if not text:
        return intent, score

    if _has_any(lowered, ("how do i", "how to", "set up", "configure", "deploy", "install")):
        intent = "how_to_steps"
        score += 0.25
    elif _has_any(lowered, ("error", "issue", "fail", "failure", "down", "not working", "troubleshoot")):
        intent = "troubleshoot"
        score += 0.3
    elif _has_any(lowered, ("compare", "versus", "vs", "difference", "better than")):
        intent = "compare_options"
        score += 0.25
    elif _has_any(lowered, ("license", "licensing", "subscription", "cost", "price", "pricing", "capacity", "sizing")):
        intent = "sizing_licensing"
        score += 0.2
    elif lowered.endswith("?") or lowered.startswith("what"):
        intent = "broad_topic_help"
        score += 0.1

    return intent, score


def classify(seed_text: str, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return dialog-oriented NLU labels for a user message."""
    text = (seed_text or "").strip()
    lowered = text.lower()
    products = _detect_products(lowered) if text else []
    topic = products[0] if products else "general"

    base_confidence = 0.0
    if text:
        base_confidence = 0.45
        if text.endswith("?"):
            base_confidence += 0.05

    intent, intent_score = _classify_intent(text)
    confidence = base_confidence + intent_score

    wants_list = _has_any(lowered, ("list", "options", "catalog", "which ones", "which products"))
    needs_scoping = False

    word_count = _word_count(text)
    if not text or word_count <= 2:
        needs_scoping = True
        confidence = min(confidence, 0.35)
    elif products and wants_list:
        confidence += 0.05
    elif not products and intent in {"broad_topic_help", "how_to_steps"} and word_count < 6:
        needs_scoping = True
        confidence = min(confidence, 0.4)

    if products:
        confidence += 0.15
    if _has_any(lowered, ("detail", "deep dive", "explain", "explanation")):
        confidence += 0.05

    expected_depth = "brief" if _has_any(lowered, ("quick", "short", "brief", "tl;dr", "overview", "high level")) else "normal"

    if intent == "compare_options" and wants_list:
        confidence += 0.05

    confidence = max(0.0, min(1.0, confidence))

    return {
        "topic": topic,
        "intent": intent,
        "needs_scoping": needs_scoping,
        "wants_list": wants_list,
        "expected_depth": expected_depth,
        "confidence": round(confidence, 3),
        "products": products,
    }

