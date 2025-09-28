import re
from typing import Dict, List, Tuple

def infer_intent(text: str, persona_id: str, store) -> Dict:
    """Regex over persona_intent_patterns; pick highest weight match."""
    patterns: List[Tuple[str, str, float]] = store.fetch_intent_patterns(persona_id)
    best = None
    for name, pat, w in patterns:
        try:
            if re.search(pat, text, re.I):
                score = w
                if not best or score > best[2]:
                    best = (name, pat, score)
        except re.error:
            if pat.lower() in text.lower():
                score = w * 0.5
                if not best or score > best[2]:
                    best = (name, pat, score)
    if best:
        conf = min(0.99, 0.5 + best[2] * 0.5)
        return {"intent": best[0], "confidence": conf}
    return {"intent": "fallback", "confidence": 0.2}

def extract_entities(intent: str, text: str) -> Dict:
    ents: Dict[str, str] = {}
    if intent == "flasharray_install":
        if re.search(r"\biscsi\b", text, re.I): ents["protocol"] = "iscsi"
        if re.search(r"\bfc\b",    text, re.I): ents["protocol"] = "fc"
        m = re.search(r"\b(rhel|ubuntu|windows|esxi)\b", text, re.I)
        if m: ents["host_os"] = m.group(1).lower()
    return ents
