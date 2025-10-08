# app/personas/prompt_builder.py
from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Tuple, Optional

# ----------------------------
# Constants / knobs (tunable)
# ----------------------------
TEACHER_MOVES: Tuple[str, ...] = (
    "check_understanding",
    "offer_steps",
    "summarize_next_actions",
    "deep_dive",
    "compare",
    "visualize",
)
MAX_FEWSHOTS: int = 4  # cap few-shots to keep prompt tight in production


# ----------------------------
# Helpers
# ----------------------------
def _hash_prompt(parts: List[str]) -> str:
    """Deterministic short hash of the built prompt for telemetry."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _choose_teacher_move(weights: Dict[str, float], dialog_meta: Dict[str, Any]) -> str:
    """
    Pick one teacher move.
      - If dialog_meta declares a move, honor it.
      - Else sample by weights (fallback to summarize_next_actions).
    """
    explicit = (dialog_meta or {}).get("teacher_move")
    if isinstance(explicit, str) and explicit in TEACHER_MOVES:
        return explicit

    # Weighted choice
    bag: List[str] = []
    for k in TEACHER_MOVES:
        try:
            w = float(weights.get(k, 0.0))
        except Exception:
            w = 0.0
        if w > 0:
            bag.extend([k] * max(1, int(round(w * 100))))
    return random.choice(bag) if bag else "summarize_next_actions"


def _stable_quote_id(text: str, *, bank: Optional[str] = None, candidate_id: Optional[str] = None) -> str:
    """Generate a deterministic identifier for a persona quote."""
    base: str
    if candidate_id:
        base = str(candidate_id)
    else:
        salted = f"{bank or ''}::{text}" if bank else text
        base = hashlib.sha1(salted.encode("utf-8", errors="ignore")).hexdigest()
    return base[:12]


def _normalize_toggle_value(value: Any) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "none"}:
            return None
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        try:
            return float(lowered)
        except Exception:
            return value.strip()
    try:
        return bool(value)
    except Exception:
        return None


def _extract_toggle(cfg: Dict[str, Any], name: str) -> Optional[Any]:
    candidates = (
        name,
        f"{name}_level",
        f"gen_{name}",
        f"allow_{name}",
        f"{name}_enabled",
    )
    for key in candidates:
        if key in cfg:
            return _normalize_toggle_value(cfg.get(key))
    return None


def _collect_guardrail_markers(dialog_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Capture guardrail-suppressed persona signals from dialog metadata."""
    muted: List[str] = []

    def _extend(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            val = value.strip()
            if val:
                muted.append(val)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _extend(item)
            return
        if isinstance(value, dict):
            for key, flag in value.items():
                if isinstance(flag, bool) and not flag:
                    continue
                if flag:
                    muted.append(str(key))
            return
        try:
            if bool(value):
                muted.append(str(value))
        except Exception:
            pass

    meta = dialog_meta or {}
    for key in (
        "persona_guardrail_muted",
        "persona_guardrail_suppressed",
        "guardrail_persona_muted",
        "guardrail_persona_suppressed",
    ):
        _extend(meta.get(key))

    guardrail_section = meta.get("guardrail") or meta.get("guardrails")
    if isinstance(guardrail_section, dict):
        for subkey in ("persona", "persona_muted", "muted"):
            _extend(guardrail_section.get(subkey))

    return {"muted": sorted({m for m in muted if m})}


def _maybe_persona_quote(cfg: Dict[str, Any], intensity: float) -> Tuple[str, Dict[str, Any]]:
    """Return a light persona quote ~intensity fraction of the time along with metadata."""
    bank_name: Optional[str] = None
    enabled = True
    candidates: List[Tuple[str, Optional[str]]] = []

    quotes_cfg = cfg.get("quotes")
    if isinstance(quotes_cfg, dict):
        enabled = bool(quotes_cfg.get("enabled", enabled))
        bank_name = str(quotes_cfg.get("bank") or quotes_cfg.get("id") or "") or None
        raw_candidates = (
            quotes_cfg.get("choices")
            or quotes_cfg.get("values")
            or quotes_cfg.get("samples")
            or quotes_cfg.get("quotes")
        )
        if isinstance(raw_candidates, dict):
            for key, value in raw_candidates.items():
                text = str(value or "").strip()
                if text:
                    candidates.append((text, str(key)))
        elif isinstance(raw_candidates, (list, tuple)):
            for item in raw_candidates:
                if isinstance(item, dict):
                    text = str(
                        item.get("text")
                        or item.get("quote")
                        or item.get("value")
                        or ""
                    ).strip()
                    qid = item.get("id") or item.get("key") or item.get("uuid")
                    if text:
                        candidates.append((text, str(qid) if qid is not None else None))
                else:
                    text = str(item or "").strip()
                    if text:
                        candidates.append((text, None))

    raw_bank = cfg.get("quote_bank")
    if isinstance(raw_bank, dict):
        for key, value in raw_bank.items():
            text = str(value or "").strip()
            if text:
                candidates.append((text, str(key)))
    elif isinstance(raw_bank, (list, tuple)):
        for item in raw_bank:
            text = str(item or "").strip()
            if text:
                candidates.append((text, None))
    elif isinstance(raw_bank, str) and raw_bank and not bank_name:
        bank_name = raw_bank

    meta = {
        "bank": bank_name,
        "enabled": bool(enabled and candidates),
        "candidate_count": len(candidates),
        "quote_id": None,
        "text": "",
        "picked": False,
    }

    if not meta["enabled"]:
        return "", meta

    try:
        p = max(0.0, min(1.0, float(intensity)))
        if random.random() <= p:
            chosen_text, candidate_id = random.choice(candidates)
            meta["picked"] = True
            meta["text"] = chosen_text
            meta["quote_id"] = _stable_quote_id(
                chosen_text,
                bank=meta.get("bank"),
                candidate_id=candidate_id,
            )
            return chosen_text, meta
    except Exception:
        pass
    return "", meta


def _coerce_str_map(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}


def _is_greet(dialog_meta: Optional[Dict[str, Any]]) -> bool:
    """Robust greet detection across multiple meta fields."""
    try:
        meta = dialog_meta or {}
        src = str(meta.get("source", "")).strip().lower()
        seed = str(meta.get("seed_text", "")).strip().lower()
        kind = str(meta.get("turn_kind", "")).strip().lower()
        intent = str(meta.get("intent", "")).strip().lower()
        greet_markers = {"greet", "ws_greet", "greet_fallback", "greeting", "welcome"}
        return (src in greet_markers) or (seed in greet_markers or seed == "greet") \
            or (kind in greet_markers) or (intent in {"greet", "opening"})
    except Exception:
        return False


def _first_name_from_profile(user_profile: Optional[Dict[str, Any]]) -> str:
    """Best-effort: prefer first_name; else split 'name'/'full_name' on space; else ''."""
    try:
        p = user_profile or {}
        fn = str(p.get("first_name") or "").strip()
        if fn:
            return fn
        nm = str(p.get("name") or p.get("full_name") or "").strip()
        if nm:
            # Avoid greeting with emails or long identifiers
            if "@" in nm:
                return ""
            return nm.split()[0]
        return ""
    except Exception:
        return ""


# ----------------------------
# Main
# ----------------------------
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
    (messages, teacher_move, prompt_hash, persona_trace)
    """
    dialog_meta = dialog_meta or {}
    cfg: Dict[str, Any] = _coerce_str_map(persona.get("config"))

    # Clamp intensity into [0,1] and default to 0.13 (12–15% persona presence target)
    try:
        intensity = float(persona.get("intensity", 0.13))
    except Exception:
        intensity = 0.13
    intensity = max(0.0, min(1.0, intensity))

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
    quote, quote_meta = _maybe_persona_quote(cfg, intensity)
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
    dev_lines: List[str] = []

    # If this is a greet turn, steer to a welcoming opener; otherwise allow short acks.
    is_greet = _is_greet(dialog_meta)
    user_first = _first_name_from_profile(user_profile)

    if is_greet:
        if user_first:
            dev_lines.insert(
                0,
                f'For greet: start with a warm, brief welcome as Chip and address the user by first name '
                f'(e.g., "Hello {user_first}, how can I help today?"). Do not say "Got it."'
            )
        else:
            dev_lines.insert(
                0,
                'For greet: start with a warm, brief welcome as Chip (e.g., "Hey—I’m Chip. Ready when you are."). '
                'Do not say "Got it."'
            )
    else:
        dev_lines.insert(
            0,
            'For non-greet: you may start with a short human acknowledgement only if it adds clarity '
            '(e.g., "Good question."). Avoid "Got it." unless explicitly asked.'
        )

    # Core dev guidance (applies to all turns)
    dev_lines.extend([
        "Never stall; respond in 1–2 sentences before any bullets.",
        "Prefer concise sentences; use bullets only when listing true options/steps.",
        "Avoid over-formality. Sound like a knowledgeable, friendly engineer.",
        "If the user seems uncertain, ask one brief clarifying question, then proceed.",
        "Keep empathy short and professional; do not overemote.",
    ])

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "\n".join(sys_lines)},
        {"role": "developer", "content": "\n".join(dev_lines)},
    ]

    # ---------- FEW-SHOTS (optional, before user) ----------
    # Accept either {"pattern": "...", "assistant": "..."} or {"user":"...","assistant":"..."}
    if examples:
        count = 0
        for ex in examples:
            if count >= MAX_FEWSHOTS:
                break
            user_ex = ex.get("user") or ex.get("pattern")
            asst_ex = ex.get("assistant") or ex.get("assistant_target")
            if user_ex and asst_ex:
                messages.append({"role": "user", "content": str(user_ex)})
                messages.append({"role": "assistant", "content": str(asst_ex)})
                count += 1

    # ---------- USER MESSAGE ----------
    messages.append({"role": "user", "content": str(user_text)})

    # Hash for telemetry (full message roles/contents)
    prompt_hash = _hash_prompt([f"{m['role']}:{m['content']}" for m in messages])

    toggles: Dict[str, Any] = {}
    for toggle_name in ("humor", "metaphor"):
        value = _extract_toggle(cfg, toggle_name)
        if value is not None:
            toggles[toggle_name] = value

    persona_trace = {
        "intensity": intensity,
        "quote": quote_meta,
        "toggles": toggles,
        "guardrail": _collect_guardrail_markers(dialog_meta),
    }

    return messages, teacher_move, prompt_hash, persona_trace
