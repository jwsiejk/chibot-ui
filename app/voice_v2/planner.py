"""Lightweight dialog planner for AskChip voice v2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal

PlanMode = Literal["clarify", "outline", "deep_dive", "compare", "steps", "next_actions"]


@dataclass
class Plan:
    """Represents the planner outcome for a user turn."""

    mode: PlanMode
    missing_info: List[str]
    chips: List[str]
    reason: str


_MODE_CHIPS: Dict[PlanMode, List[str]] = {
    "clarify": [
        "Confirm Pure Storage product",
        "Clarify customer workload",
        "Ask for timeline",
    ],
    "outline": [
        "State customer objective",
        "Highlight Pure differentiators",
        "Note partner resources",
    ],
    "deep_dive": [
        "Detail FlashArray configuration",
        "Discuss data reduction plan",
        "Cover integration dependencies",
    ],
    "compare": [
        "FlashArray vs FlashBlade fit",
        "Differentiate Evergreen benefits",
        "Match workload to model",
    ],
    "steps": [
        "Run environment precheck",
        "Validate network zoning",
        "Schedule go-live rehearsal",
    ],
    "next_actions": [
        "Share Pure enablement link",
        "Coordinate customer workshop",
        "Log follow-up in CRM",
    ],
}


def plan_turn(text: str) -> Plan:
    """Select the dialog mode and supporting metadata for a user utterance."""

    normalized = (text or "").strip().lower()
    word_count = len([token for token in normalized.split() if token])

    if word_count < 5:
        mode: PlanMode = "clarify"
        reason = "short utterance"
        missing = ["intent", "details"]
    elif any(token in normalized for token in [" vs ", "compare", "versus"]):
        mode = "compare"
        reason = "comparison cue"
        missing = []
    elif any(keyword in normalized for keyword in ["step", "guide", "walkthrough", "setup", "install"]):
        mode = "steps"
        reason = "steps cue"
        missing = []
    else:
        mode = "outline"
        reason = "default outline"
        missing = []

    chips = list(_MODE_CHIPS.get(mode, []))
    return Plan(mode=mode, missing_info=missing, chips=chips, reason=reason)
