import re
from typing import Dict, List, Tuple


def _score_to_conf(score: float) -> float:
    return min(0.99, 0.5 + score * 0.5)

def infer_intent(text: str, persona_id: str, store) -> Dict:
    """Regex over persona_intent_patterns; pick highest weight match."""
    patterns: List[Tuple[str, str, float]] = store.fetch_intent_patterns(persona_id)
    matches: Dict[str, float] = {}
    for name, pat, w in patterns:
        try:
            if re.search(pat, text, re.I):
                score = float(w)
                matches[name] = max(score, matches.get(name, float("-inf")))
        except re.error:
            if pat.lower() in text.lower():
                score = float(w) * 0.5
                matches[name] = max(score, matches.get(name, float("-inf")))
    if matches:
        ranked = sorted(matches.items(), key=lambda item: item[1], reverse=True)
        top_intent, top_score = ranked[0]
        result = {"intent": top_intent, "confidence": _score_to_conf(top_score)}
        alternates = []
        for intent, score in ranked[1:3]:
            alternates.append({"intent": intent, "confidence": _score_to_conf(score)})
        if alternates:
            result["alternates"] = alternates
        return result
    return {"intent": "fallback", "confidence": 0.2, "alternates": []}

def extract_entities(intent: str, text: str) -> Dict:
    ents: Dict[str, str] = {}
    if intent == "flasharray_install":
        if re.search(r"\biscsi\b", text, re.I): ents["protocol"] = "iscsi"
        if re.search(r"\bfc\b",    text, re.I): ents["protocol"] = "fc"
        m = re.search(r"\b(rhel|ubuntu|windows|esxi)\b", text, re.I)
        if m: ents["host_os"] = m.group(1).lower()
    return ents
