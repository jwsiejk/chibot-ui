# app/personas/prompt_builder.py
from __future__ import annotations
import hashlib
import random
from typing import Any, Dict, List, Tuple, Optional

TEACHER_MOVES = (
    "check_understanding",
    "offer_steps",
    "summarize_next_actions",
    "deep_dive",
    "compare",
    "visualize",
)

def _hash_prompt(parts: List[str]) -> str:
    """Deterministic short hash of the built prompt for telemetry."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]

def _choose_teacher_move(weights: Dict[str, float], dialog_meta: Dict[str, Any]) -> str:
    """
    Pick one teacher move.
    - If dialog_meta declares a move, honor it.
    - Else sample by weights (fallback to summarize_next_actions).
    """
    explicit = (dialog_meta or {}).get("teacher_move")
    if explicit in TEACHER_MOVES:
        return explicit

    # Weighted choice
    bag: List[str] = []
    for k in TEACHER_MOVES:
        w = float(weights.get(k, 0.0))
        if w > 0:
            bag += [k] * max(1, int(round(w * 100)))
    return random.choice(bag) if bag else "summarize_next_actions"

def _maybe_persona_quote(cfg: Dict[str, Any], intensity: float) -> str:
    """Return a light persona quote ~intensity fraction of the time."""
    try:
        if random.random() <= max(0.0, min(1.0, float(intensity))):
            q = cfg.get("quote_bank") or []
            if q:
                return random.choice(q)
    except Exception:
        pass
    return ""

def _coerce_str_map(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}

def build_messages(
    *,
    persona: Dict[str, Any],
    session_summary: str = "",
    user_profile: Optional[Dict[str, Any]] = None,
    user_text: str,
    dialog_meta: Optional[Dict[str, Any]] = None,
    examples: Optional[List[Dict[str, str]]] = None,
) -> Tuple[List[Dict[str, str]], str, str]:
    """
    Compose the chat messages for the LLM.

    Parameters
    ----------
    persona: dict
        {'id','name','intensity','config':{...}} as returned by PersonaManager.get_active()
    session_summary: str
        Short rolling summary to keep context tight.
    user_profile: dict|None
        Optional profile details to personalize responses.
    user_text: str
        The user's current utterance (final transcript).
    dialog_meta: dict|None
        Runtime signals (intent, prosody flags, overrides, etc.)
        Example: {"intent":"flasharray_install", "teacher_move":"offer_steps", "barge_in":True}
    examples: list|None
        Optional few-shots to prepend. Each item may have keys:
        - {"pattern": "<user example>", "assistant": "<ideal assistant reply>"}
        - or {"user": "...", "assistant": "..."}

    Returns
    -------
    (messages, teacher_move, prompt_hash)
    """
    dialog_meta = dialog_meta or {}
    cfg: Dict[str, Any] = _coerce_str_map(persona.get("config"))
    intensity = float(persona.get("intensity", 0.13))

    # Persona knobs
    tone = _coerce_str_map(cfg.get("tone"))
    style = _coerce_str_map(cfg.get("style_rules"))
    forbidden = cfg.get("forbidden") or []
    goals = cfg.get("goals") or []
    weights = _coerce_str_map(cfg.get("teacher_moves_weights"))
    lexicon = _coerce_str_map(cfg.get("lexicon"))
    kb = _coerce_str_map(cfg.get("kb"))
    llm_instructions: List[str] = list(cfg.get("llm_instructions") or [])

    # Decide teacher move
    teacher_move = _choose_teacher_move(weights, dialog_meta)

    # Optional light persona quote
    quote = _maybe_persona_quote(cfg, intensity)
    quote_line = f'Persona note: "{quote}"' if quote else ""

    # ---------- SYSTEM MESSAGE ----------
    sys_lines: List[str] = []
    sys_lines.append(f"You are {persona.get('name','Chip')} — a Virtual Partner Technical Manager supporting Trace3 on Pure Storage.")
    sys_lines.append("Never mention you are an AI or a chatbot. Speak naturally, like you’re in a Zoom-style session.")
    sys_lines.append("Use contractions, keep sentences short, prefer bullets when helpful, and end with a clear next step.")
    sys_lines.append(f"Teacher move for this turn: {teacher_move}.")
    # Persona-configured tone/style/policy
    if tone:
        sys_lines.append(
            f"Tone defaults: default='{tone.get('default','supportive')}', "
            f"error='{tone.get('error','calm')}', teaching='{tone.get('teaching','encouraging')}'."
        )
    if goals:
        sys_lines.append(f"Goals: {goals}")
    if style:
        sys_lines.append(f"Style rules: {style}")
    if forbidden:
        sys_lines.append(f"Forbidden: {forbidden}")
    if kb:
        sys_lines.append(f"KB policy: {kb}")
    if lexicon:
        sys_lines.append(f"Lexicon normalization note (post-STT, pre-LLM): {lexicon}")

    # Persona-specific llm instructions (from DB)
    for rule in llm_instructions:
        if isinstance(rule, str) and rule.strip():
            sys_lines.append(rule.strip())

    if quote_line:
        sys_lines.append(quote_line)

    if session_summary:
        sys_lines.append(f"Session summary: {session_summary}")

    if user_profile:
        sys_lines.append(f"User profile: {user_profile}")

    # ---------- DEVELOPER MESSAGE ----------
    dev_lines = [
        "If unsure, briefly state assumptions, ask one concise clarifier if needed, then proceed.",
        "Prefer ≤4 bullets or ≤6 sentences unless the user explicitly asks for more.",
        "Keep openings human and short (e.g., “Got it.” / “Good question.”) then content.",
    ]
    if dialog_meta:
        dev_lines.append(f"Dialog meta: {dialog_meta}")

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "\n".join(sys_lines)},
        {"role": "developer", "content": "\n".join(dev_lines)},
    ]

    # ---------- FEW-SHOTS (optional, before user) ----------
    # Accept either {"pattern": "...", "assistant": "..."} or {"user":"...","assistant":"..."}
    if examples:
        for ex in examples:
            user_ex = ex.get("user") or ex.get("pattern")
            asst_ex = ex.get("assistant") or ex.get("assistant_target")
            if user_ex and asst_ex:
                messages.append({"role": "user", "content": str(user_ex)})
                messages.append({"role": "assistant", "content": str(asst_ex)})

    # ---------- USER MESSAGE ----------
    messages.append({"role": "user", "content": user_text})

    # Hash for telemetry
    prompt_hash = _hash_prompt([f"{m['role']}:{m['content']}" for m in messages])

    return messages, teacher_move, prompt_hash
