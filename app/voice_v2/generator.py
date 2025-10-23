"""Rule-based NLG helpers that transform persona data into prompts and replies."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from app.voice_v2.persona import build_system_preamble


def _coerce_mode(plan: object) -> str | None:
    if isinstance(plan, Mapping):
        mode = plan.get("mode")
        return mode if isinstance(mode, str) and mode else None
    return getattr(plan, "mode", None)


def build_messages(persona: Mapping[str, Any], plan: Mapping[str, Any] | object, user_text: str) -> List[Dict[str, str]]:
    """Construct the conversational message stack for persona-directed generation."""

    system_content = build_system_preamble(persona)
    modes = persona.get("modes") if isinstance(persona, Mapping) else None
    mode_key = _coerce_mode(plan)
    developer_instruction = ""
    if isinstance(modes, Mapping) and isinstance(mode_key, str):
        candidate = modes.get(mode_key)
        if isinstance(candidate, Mapping):
            instruction = candidate.get("instruction")
            if isinstance(instruction, str):
                developer_instruction = instruction
    developer_instruction = developer_instruction or "Stay helpful, concise, and focused on Pure Storage outcomes."

    user_content = (user_text or "").strip()

    return [
        {"role": "system", "content": system_content},
        {"role": "developer", "content": developer_instruction},
        {"role": "user", "content": user_content},
    ]


def _detect_mode(instruction: str) -> str:
    normalized = instruction.lower()
    if "ask concise follow-ups" in normalized:
        return "clarify"
    if "step-by-step" in normalized or "checkpoints" in normalized:
        return "steps"
    if "contrast" in normalized:
        return "compare"
    if "walk through" in normalized:
        return "deep_dive"
    if "assign the immediate" in normalized:
        return "next_actions"
    if "summarize" in normalized:
        return "outline"
    return "outline"


def _combine(sentences: Sequence[str]) -> str:
    return " ".join(sentence.strip() for sentence in sentences if sentence).strip()


def _topic_from_user(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned:
        return cleaned
    return "this opportunity"


def _clarify_reply(topic: str) -> List[str]:
    _ = topic  # compatibility placeholder; clarify keeps legacy greeting for now
    return ["Hi there! How can I help you today?"]


def _outline_reply(topic: str) -> List[str]:
    return [
        f"Here's the Pure Storage path I'd frame for {topic}.",
        "Lead with the customer's objective, map it to the right FlashArray or FlashBlade fit, and spotlight the architectural pillars that deliver it.",
        "Close by pointing to the partner resources and success metrics that prove the outcome.",
    ]


def _deep_dive_reply(topic: str) -> List[str]:
    return [
        f"Let's walk the technical layers of the design for {topic}.",
        "Start with array sizing and data reduction planning, then cover connectivity, failover domains, and any integration dependencies the partner needs to stage.",
        "Wrap with validation steps so the team sees how each choice ties back to measurable customer outcomes.",
    ]


def _compare_reply(topic: str) -> List[str]:
    return [
        f"To compare options for {topic}, anchor on the workload fit.",
        "Contrast how FlashArray and FlashBlade handle the performance profile, Evergreen economics, and management effort.",
        "Finish by naming the option that best aligns to the requirement and why it keeps the customer moving fast.",
    ]


def _steps_reply(topic: str) -> List[str]:
    return [
        f"Let's map the rollout for {topic}.",
        "Step 1: Run an environment precheck so array connectivity, hosts, and power are ready.",
        "Step 2: Validate network zoning and replication design with the partner team before cutover.",
        "Step 3: Schedule a go-live rehearsal and document checkpoints for customer sign-off.",
    ]


def _next_actions_reply(topic: str) -> List[str]:
    return [
        f"Here are the actions to keep {topic} moving.",
        "Share the enablement assets that reinforce the value story and align on the next technical workshop.",
        "Log the follow-up in CRM with owner and due date so we maintain momentum after this call.",
    ]


_MODE_TO_REPLY = {
    "clarify": _clarify_reply,
    "outline": _outline_reply,
    "deep_dive": _deep_dive_reply,
    "compare": _compare_reply,
    "steps": _steps_reply,
    "next_actions": _next_actions_reply,
}


def render_reply(messages: Sequence[Mapping[str, Any]]) -> str:
    """Create a deterministic assistant reply that follows the developer instruction."""

    developer_instruction = ""
    user_text = ""
    for message in messages:
        role = message.get("role") if isinstance(message, Mapping) else None
        if role == "developer" and not developer_instruction:
            content = message.get("content") if isinstance(message, Mapping) else None
            if isinstance(content, str):
                developer_instruction = content
        elif role == "user" and not user_text:
            content = message.get("content") if isinstance(message, Mapping) else None
            if isinstance(content, str):
                user_text = content

    mode = _detect_mode(developer_instruction)
    topic = _topic_from_user(user_text)
    reply_builder = _MODE_TO_REPLY.get(mode, _outline_reply)
    sentences = reply_builder(topic)
    if len(sentences) < 2 and mode != "clarify":
        sentences.append("Let me know if you want me to expand on any part of that.")
    if len(sentences) > 5:
        sentences = list(sentences[:5])
    return _combine(sentences)


__all__ = ["build_messages", "render_reply"]
