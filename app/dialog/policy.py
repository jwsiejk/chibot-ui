"""Policy heuristics that map interpreter output to dialog frames."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..nlu.universal_interpreter import ensure_all_fields as _ensure_universal_fields

_CLARIFY_THRESHOLD = 0.45
_SUGGESTION_ACTIONS = {"clarify", "offer_steps"}

_MISSING_CHIP_LIBRARY = {
    "details": "Add more detail",
    "intent": "Tell me your goal",
    "product": "Name the product",
    "issue_detail": "Describe the issue",
    "message": "Repeat the request",
}

_DEFAULT_CHIPS = ["Share more context", "What are you solving?", "Show an example"]


def _coerce_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def _normalize_str(value: Any) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


def _confirmed_set(goal: Optional[Mapping[str, Any]]) -> Sequence[str]:
    if not isinstance(goal, Mapping):
        return []
    confirmed = goal.get("confirmed")
    if isinstance(confirmed, Iterable) and not isinstance(confirmed, (str, bytes)):
        return [str(item) for item in confirmed if item is not None]
    if isinstance(confirmed, str):
        return [confirmed]
    return []


def _has_goal_entity(goal: Optional[Mapping[str, Any]], key: str) -> bool:
    if not isinstance(goal, Mapping):
        return False
    entities = goal.get("entities")
    if not isinstance(entities, Mapping):
        return False
    items = entities.get(key)
    if isinstance(items, Mapping):
        items = list(items.values())
    if isinstance(items, Iterable) and not isinstance(items, (str, bytes)):
        return any(str(item).strip() for item in items)
    return bool(items)


def _missing_for_clarify(universal: Mapping[str, Any], goal: Optional[Mapping[str, Any]]) -> List[str]:
    raw_missing = universal.get("missing") if isinstance(universal, Mapping) else None
    missing: List[str] = []
    if isinstance(raw_missing, Iterable) and not isinstance(raw_missing, (str, bytes)):
        for item in raw_missing:
            text = _normalize_str(item)
            if text:
                missing.append(text)
    elif isinstance(raw_missing, str):
        normalized = _normalize_str(raw_missing)
        if normalized:
            missing.append(normalized)

    confirmed = {m.lower() for m in _confirmed_set(goal)}
    filtered: List[str] = []
    for key in missing:
        if key in confirmed:
            continue
        if key == "product" and _has_goal_entity(goal, "products"):
            continue
        if key == "intent" and _normalize_str(goal.get("working_intent")):
            continue
        filtered.append(key)
    return filtered


def _chips_from_missing(missing: Sequence[str]) -> List[str]:
    chips: List[str] = []
    for key in missing:
        choice = _MISSING_CHIP_LIBRARY.get(key)
        if choice and choice not in chips:
            chips.append(choice)
        if len(chips) >= 3:
            break
    if chips:
        return chips
    return list(_DEFAULT_CHIPS[:3])


def _is_unknown_field(value: Any) -> bool:
    normalized = _normalize_str(value)
    if not normalized:
        return True
    return normalized in {"unknown", "undetermined"}


def _resolve_delivery_frame(universal: Mapping[str, Any],
                            nlu_result: Mapping[str, Any],
                            missing: Sequence[str]) -> str:
    delivery = _normalize_str(universal.get("delivery_pref"))
    if delivery == "summary":
        return "summarize_next_actions"
    if delivery == "steps":
        return "offer_steps"
    if delivery in {"list", "compare"}:
        return "compare"

    if bool(nlu_result.get("wants_list")):
        return "compare"

    user_goal = _normalize_str(universal.get("user_goal"))
    depth = _normalize_str(universal.get("depth"))

    if user_goal == "complete_task":
        return "offer_steps"
    if user_goal in {"compare_options", "plan_capacity"}:
        return "compare"
    if user_goal == "resolve_issue":
        return "clarify" if missing else "deep_dive"
    if user_goal == "greeting":
        return "high_level"

    if depth == "deep":
        return "deep_dive"
    return "high_level"


def pick(nlu_result: Dict[str, Any],
         universal_result: Optional[Dict[str, Any]] = None,
         *,
         session_goal: Optional[Dict[str, Any]] = None,
         clarify_threshold: float = _CLARIFY_THRESHOLD) -> Dict[str, Any]:
    """Select a dialog frame based on interpreter output."""

    nlu_result = nlu_result or {}
    universal = _ensure_universal_fields(universal_result)
    goal = session_goal or {}

    confidence = _coerce_float(universal.get("confidence"), default=0.0)
    intent = _normalize_str(nlu_result.get("intent"))
    is_greet = intent in {"greet", "idle"}
    needs_clarification = bool(universal.get("needs_clarification"))
    missing = _missing_for_clarify(universal, goal)
    if is_greet:
        missing = []

    should_clarify = (
        needs_clarification
        or confidence < clarify_threshold
        or any(_is_unknown_field(universal.get(field)) for field in ("phase", "depth", "delivery_pref"))
    )
    if is_greet:
        should_clarify = False

    action: str
    chips: List[str] = []
    frame_meta: Dict[str, Any] = {}

    if should_clarify:
        action = "clarify"
        chips = _chips_from_missing(missing)
        frame_meta["clarify_targets"] = missing or ["details"]
    else:
        action = _resolve_delivery_frame(universal, nlu_result, missing)
        if action == "clarify":
            chips = _chips_from_missing(missing)
            frame_meta["clarify_targets"] = missing or ["details"]

    if is_greet:
        action = "offer_steps"
        chips = []
        frame_meta = {}

    depth_value = _normalize_str(universal.get("depth"))
    if is_greet:
        depth_value = "brief"
    verbosity = "brief" if action == "clarify" or depth_value == "brief" else "normal"

    show_suggestions = action in _SUGGESTION_ACTIONS or bool(chips)

    result: Dict[str, Any] = {
        "action": action,
        "teacher_move": action,
        "frame": action,
        "verbosity": verbosity,
        "show_suggestions": bool(show_suggestions),
    }
    if chips:
        result["chips"] = chips
    if frame_meta:
        result.update(frame_meta)

    return result


__all__ = ["pick"]

