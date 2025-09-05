# app/services/dialog_policy.py
from typing import Dict

ALLOWED_MOVES = ("check_understanding","deep_dive","offer_steps","compare","visualize","summarize_next_actions")
ALLOWED_TONES = ("brief","empathetic","energetic")

def pick(labels: Dict, cfg: Dict) -> Dict:
    if not cfg.get("awareness_enabled", True):
        return {"teacher_move":"offer_steps","tone":"brief"}
    sent = labels.get("sentiment")
    eng  = labels.get("engagement")
    move, tone = "offer_steps","brief"
    if sent == "frustrated":
        move, tone = "summarize_next_actions","brief"
    elif sent == "uncertain":
        move, tone = "check_understanding","empathetic"
    elif eng == "high":
        move, tone = "deep_dive","energetic"
    # Respect allowlists if present
    if cfg.get("policy_teacher_moves"):
        if move not in cfg["policy_teacher_moves"]: move = cfg["policy_teacher_moves"][0]
    if cfg.get("policy_tones"):
        if tone not in cfg["policy_tones"]: tone = cfg["policy_tones"][0]
    return {"teacher_move": move, "tone": tone}
