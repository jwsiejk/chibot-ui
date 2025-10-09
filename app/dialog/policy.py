"""Policy heuristics that map interpreter output to dialog frames."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..nlu.universal_interpreter import ensure_all_fields as _ensure_universal_fields
from ..services import config_store

_CLARIFY_THRESHOLD = 0.45
_PLANNER_HYSTERESIS = 0.05

_BAND_ORDER = {"low": 0, "medium": 1, "high": 2}
_DEFAULT_PLANNER_THRESHOLDS = {
    "low": 0.0,
    "medium": 0.60,
    "high": 0.75,
}

_BAND_CHIPS = {
    "medium": ["Share more context", "What are you solving?", "Show an example"],
    "low": ["Ask a question", "Explain how it works", "Project advice"],
}
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


def _configured_default_planner_thresholds() -> Dict[str, float]:
    defaults = dict(_DEFAULT_PLANNER_THRESHOLDS)
    try:
        configured = config_store.get_planner_threshold_defaults()
    except Exception:
        configured = None
    if isinstance(configured, Mapping):
        for key in ("low", "medium", "high"):
            if key in configured:
                try:
                    defaults[key] = float(configured[key])
                except Exception:
                    continue
    defaults["low"] = max(0.0, min(1.0, defaults.get("low", 0.0)))
    defaults["medium"] = max(defaults["low"], min(1.0, defaults.get("medium", 0.0)))
    defaults["high"] = max(defaults["medium"], min(1.0, defaults.get("high", 0.0)))
    return defaults


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


def _chips_from_missing(missing: Sequence[str], fallback: Optional[Sequence[str]] = None) -> List[str]:
    chips: List[str] = []
    for key in missing:
        choice = _MISSING_CHIP_LIBRARY.get(key)
        if choice and choice not in chips:
            chips.append(choice)
        if len(chips) >= 3:
            break
    if chips:
        return chips
    if fallback is None:
        fallback = _DEFAULT_CHIPS
    return list(fallback[:3])


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

    planner_defaults = _configured_default_planner_thresholds()
    fallback_medium = max(planner_defaults.get("medium", 0.0), clarify_threshold)
    thresholds = _resolve_planner_thresholds(
        universal,
        goal,
        fallback_medium,
        defaults=planner_defaults,
    )
    last_band = _extract_last_band(goal)
    confidence_band = _select_band(confidence, thresholds, last_band)

    unknown_fields = any(
        _is_unknown_field(universal.get(field)) for field in ("phase", "depth", "delivery_pref")
    )

    if missing:
        confidence_band = "medium"
    elif needs_clarification or unknown_fields:
        confidence_band = "low"

    action: str
    frame_name: str
    chips: List[str] = []
    frame_meta: Dict[str, Any] = {}

    if is_greet:
        confidence_band = "high"

    clarify_variant: Optional[str] = None

    if confidence_band == "high" and not (needs_clarification or unknown_fields or missing):
        action = _resolve_delivery_frame(universal, nlu_result, missing)
        frame_name = action
    else:
        if missing:
            confidence_band = "medium"
        elif confidence_band not in {"medium", "low"}:
            confidence_band = "low"
        action = "clarify"
        frame_name = "clarify"
        clarify_variant = "specific" if confidence_band == "medium" else "high_level"
        chips = _chips_for_band(confidence_band, missing)
        frame_meta["clarify_targets"] = missing or ["details"]
        frame_meta["clarify_variant"] = clarify_variant

    if is_greet:
        action = "offer_steps"
        frame_name = action
        chips = []
        frame_meta = {}

    depth_value = _normalize_str(universal.get("depth"))
    if is_greet:
        depth_value = "brief"
    verbosity = "brief" if action == "clarify" or depth_value == "brief" else "normal"

    show_suggestions = action in _SUGGESTION_ACTIONS or bool(chips)

    goal_metadata = _update_goal_band(goal, confidence_band)

    teacher_move = action

    result: Dict[str, Any] = {
        "action": action,
        "teacher_move": teacher_move,
        "frame": frame_name,
        "verbosity": verbosity,
        "show_suggestions": bool(show_suggestions),
        "confidence_band": confidence_band,
    }
    if clarify_variant:
        result["clarify_variant"] = clarify_variant
    if chips:
        result["chips"] = chips
    if frame_meta:
        result.update(frame_meta)
    if goal_metadata is not None:
        result["goal_metadata"] = goal_metadata

    return result


def _normalize_band(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        band = str(value).strip().lower()
    except Exception:
        return None
    return band if band in _BAND_ORDER else None


def _ensure_thresholds(raw: Mapping[str, Any],
                       *,
                       fallback_medium: float,
                       defaults: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    base = dict(defaults or _configured_default_planner_thresholds())
    thresholds: Dict[str, float] = {}
    for key in ("low", "medium", "high"):
        if key in raw:
            thresholds[key] = _coerce_float(raw.get(key), default=base.get(key, 0.0))
    if "medium" not in thresholds:
        thresholds["medium"] = float(fallback_medium)
    if "high" not in thresholds:
        thresholds["high"] = max(
            thresholds["medium"] + 0.2,
            base.get("high", thresholds["medium"] + 0.2),
        )
    if "low" not in thresholds:
        thresholds["low"] = base.get("low", 0.0)

    # Clamp and enforce ordering
    thresholds["low"] = max(0.0, min(1.0, thresholds["low"]))
    thresholds["medium"] = max(thresholds["low"], min(1.0, thresholds["medium"]))
    thresholds["high"] = max(thresholds["medium"], min(1.0, thresholds["high"]))
    return thresholds


def _resolve_planner_thresholds(universal: Mapping[str, Any],
                                goal: Optional[Mapping[str, Any]],
                                fallback_medium: float,
                                *,
                                defaults: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    defaults = dict(defaults or _configured_default_planner_thresholds())
    fallback_medium = max(defaults.get("medium", 0.0), fallback_medium)

    def _candidate_mappings(container: Optional[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        if not isinstance(container, Mapping):
            return
        direct = container.get("planner_thresholds")
        if isinstance(direct, Mapping):
            yield direct
        alt = container.get("planner_confidence_thresholds")
        if isinstance(alt, Mapping):
            yield alt
        metadata = container.get("metadata") if isinstance(container.get("metadata"), Mapping) else None
        if isinstance(metadata, Mapping):
            direct_meta = metadata.get("planner_thresholds")
            if isinstance(direct_meta, Mapping):
                yield direct_meta
            alt_meta = metadata.get("planner_confidence_thresholds")
            if isinstance(alt_meta, Mapping):
                yield alt_meta
        return

    for mapping in _candidate_mappings(goal):
        try:
            return _ensure_thresholds(mapping, fallback_medium=fallback_medium, defaults=defaults)
        except Exception:
            continue
    for mapping in _candidate_mappings(universal):
        try:
            return _ensure_thresholds(mapping, fallback_medium=fallback_medium, defaults=defaults)
        except Exception:
            continue

    return _ensure_thresholds(defaults, fallback_medium=fallback_medium, defaults=defaults)


def _extract_last_band(goal: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(goal, Mapping):
        return None
    for source in (goal.get("metadata") if isinstance(goal.get("metadata"), Mapping) else None, goal):
        if not isinstance(source, Mapping):
            continue
        band = source.get("planner_confidence_band") or source.get("confidence_band")
        normalized = _normalize_band(band)
        if normalized:
            return normalized
    return None


def _select_band(confidence: float,
                 thresholds: Mapping[str, float],
                 last_band: Optional[str]) -> str:
    high = float(thresholds.get("high", _DEFAULT_PLANNER_THRESHOLDS["high"]))
    medium = float(thresholds.get("medium", _DEFAULT_PLANNER_THRESHOLDS["medium"]))

    if last_band == "high" and confidence >= high - _PLANNER_HYSTERESIS:
        return "high"
    if last_band == "medium":
        if confidence >= high + _PLANNER_HYSTERESIS:
            return "high"
        if confidence < max(0.0, medium - _PLANNER_HYSTERESIS):
            return "low"
        return "medium"
    if last_band == "low":
        if confidence >= high + _PLANNER_HYSTERESIS:
            return "high"
        if confidence >= medium + _PLANNER_HYSTERESIS:
            return "medium"
        return "low"

    if confidence >= high:
        return "high"
    if confidence >= medium:
        return "medium"
    return "low"


def _chips_for_band(band: str, missing: Sequence[str]) -> List[str]:
    fallback = _BAND_CHIPS.get(band)
    if band in {"medium", "low"}:
        return _chips_from_missing(missing, fallback=fallback)
    return []


def _update_goal_band(goal: Optional[Mapping[str, Any]], band: str) -> Optional[Dict[str, Any]]:
    if not isinstance(goal, Mapping):
        return None
    metadata = goal.get("metadata") if isinstance(goal.get("metadata"), Mapping) else {}
    metadata = dict(metadata)
    metadata["planner_confidence_band"] = band
    return metadata


__all__ = ["pick"]
