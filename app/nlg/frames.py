"""Canonical response frames for Chip."""

from __future__ import annotations

from typing import Dict, Iterable

_FRAME_INSTRUCTIONS: Dict[str, str] = {
    "clarify": (
        "Frame: Clarify.\n"
        "- Ask one crisp question to close the biggest gap.\n"
        "- Offer two or three short chips drawn from the provided list.\n"
        "- Hold solutions until the user answers."
    ),
    "high_level": (
        "Frame: High-level.\n"
        "- Deliver a quick overview in two to three tight sentences.\n"
        "- Highlight only the essentials and avoid deep technical weeds.\n"
        "- Point to follow-up options if chips are available."
    ),
    "deep_dive": (
        "Frame: Deep dive.\n"
        "- Provide a thorough, well-structured explanation or walkthrough.\n"
        "- Use short paragraphs or bullets to keep it scannable.\n"
        "- Call out prerequisites, decisions, and checkpoints explicitly."
    ),
    "compare": (
        "Frame: Compare.\n"
        "- Contrast the relevant options side-by-side with concise bullets.\n"
        "- Emphasize key differences, trade-offs, and when to pick each path.\n"
        "- End by inviting the user to state their preference or constraints."
    ),
    "offer_steps": (
        "Frame: Offer steps.\n"
        "- Lay out the path as short, actionable steps in order.\n"
        "- Include who/what is responsible when it clarifies ownership.\n"
        "- Keep each step focused on a single action."
    ),
    "summarize_next_actions": (
        "Frame: Summarize next actions.\n"
        "- List the concrete follow-ups with owners or timing when possible.\n"
        "- Make the list easy to scan—one bullet per action.\n"
        "- Close with an offer to stay available or confirm alignment."
    ),
}


def list_frames() -> Iterable[str]:
    """Return the supported frame identifiers."""

    return tuple(_FRAME_INSTRUCTIONS.keys())


def get_frame_instruction(frame: str) -> str:
    """Return instructional text for the requested frame."""

    key = str(frame or "").strip().lower()
    if key in _FRAME_INSTRUCTIONS:
        return _FRAME_INSTRUCTIONS[key]
    return _FRAME_INSTRUCTIONS["high_level"]


__all__ = ["get_frame_instruction", "list_frames"]
