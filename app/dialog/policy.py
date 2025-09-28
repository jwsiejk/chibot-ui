"""Policy heuristics that map NLU output to dialog actions."""

from __future__ import annotations

from typing import Any, Dict

_SUGGESTION_ACTIONS = {"ask_clarify", "offer_steps"}


def _coerce_depth(nlu_result: Dict[str, Any]) -> str:
    depth = str(nlu_result.get("expected_depth") or "normal").strip().lower()
    if depth not in {"brief", "normal"}:
        return "normal"
    return depth


def _coerce_confidence(raw: Any) -> float:
    try:
        return float(raw)
    except Exception:
        return 0.0


def pick(nlu_result: Dict[str, Any]) -> Dict[str, Any]:
    """Select a dialog action based on the classifier output."""
    nlu_result = nlu_result or {}
    intent = (nlu_result.get("intent") or "broad_topic_help").strip().lower()
    needs_scoping = bool(nlu_result.get("needs_scoping"))
    wants_list = bool(nlu_result.get("wants_list"))
    expected_depth = _coerce_depth(nlu_result)
    confidence = _coerce_confidence(nlu_result.get("confidence"))

    action = "give_brief_answer"
    verbosity = "brief" if expected_depth == "brief" else "normal"
    show_suggestions = False

    if confidence < 0.35 or needs_scoping:
        action = "ask_clarify"
        verbosity = "brief"
        show_suggestions = True
    elif intent == "how_to_steps":
        action = "offer_steps"
        verbosity = "normal"
        show_suggestions = True
    elif intent == "troubleshoot":
        action = "summarize_next_actions"
        verbosity = "normal"
    elif intent == "compare_options":
        action = "deep_dive"
        verbosity = "normal"
        show_suggestions = wants_list
    elif intent == "sizing_licensing":
        action = "summarize_next_actions"
        verbosity = "normal"
        show_suggestions = wants_list
    elif expected_depth != "brief" and wants_list:
        action = "deep_dive"
        verbosity = "normal"
        show_suggestions = True

    result = {
        "action": action,
        "verbosity": verbosity,
        "show_suggestions": bool(show_suggestions),
    }
    if action in _SUGGESTION_ACTIONS:
        result["show_suggestions"] = True
    # Maintain backwards compatibility for existing prompt builders.
    result.setdefault("teacher_move", action)
    return result


__all__ = ["pick"]

