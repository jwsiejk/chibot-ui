from typing import Dict

DEFAULT_MOVE = "summarize_next_actions"
SUGGESTION_MOVES = {"clarify", "ask_clarify", "offer_steps"}

def decide(nlu: Dict, tags: Dict, persona_id: str, store) -> Dict:
    cfg = store.fetch_intent_config(persona_id, nlu.get("intent","")) or {}
    move = cfg.get("teacher_move") or DEFAULT_MOVE
    chips = cfg.get("chips", [])
    tool = cfg.get("tool")
    tool_payload = cfg.get("tool_payload", {})

    # Prosody overrides
    if tags.get("in_a_hurry"): move = "check_understanding"
    if tags.get("frustrated"): move = "check_understanding"

    # Low confidence → ask clarifier instead of taking action
    if nlu.get("confidence", 0) < 0.5:
        move = "check_understanding"
        chips = ["Send me the email", "Walk me through it live"]

    show_suggestions = move in SUGGESTION_MOVES
    return {
        "teacher_move": move,
        "chips": chips,
        "tool": tool,
        "tool_payload": tool_payload,
        "show_suggestions": show_suggestions,
    }
