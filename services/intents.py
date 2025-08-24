
# services/intents.py — lightweight intent & slot classifier for Chip
from __future__ import annotations
import os, re, json
from typing import Dict, Any

# Optional OpenAI client for JSON classification
_OPENAI_OK = False
try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _OPENAI_OK = True if os.getenv("OPENAI_API_KEY") else False
except Exception:
    _OPENAI_OK = False
    _client = None

# Basic regex heuristics for offline classification
_PAT_INTENTS = [
    ("how_to",       re.compile(r"\bhow\s*to\b|\binstall\b|\bconfigure\b|\bsetup\b", re.I)),
    ("troubleshoot", re.compile(r"\btroubleshoot\b|\bdebug\b|\bissue\b|\berror\b|\bfailing\b", re.I)),
    ("compare",      re.compile(r"\bcompare\b|\bversus\b|\bvs\.?\b|\bdifference\b", re.I)),
    ("upgrade",      re.compile(r"\bupgrade\b|\bmigrate\b|\bupdate\b", re.I)),
    ("design",       re.compile(r"\bdesign\b|\barchitecture\b|\barch\b|\bsize\b|\bsizing\b", re.I)),
    ("info",         re.compile(r"\boverview\b|\bexplain\b|\bwhat\s+is\b|\bsummary\b", re.I)),
]

_PAT_PRODUCTS = [
    ("Portworx",     re.compile(r"\bport\s*worx\b|\bportworx\b|\bpx\b", re.I)),
    ("FlashArray",   re.compile(r"\bflash\s*array\b|\bflasharray\b|\bfa\b", re.I)),
    ("FlashBlade",   re.compile(r"\bflash\s*blade\b|\bflashblade\b|\bfb\b", re.I)),
    ("PX-Backup",    re.compile(r"\bpx\s*[- ]?backup\b", re.I)),
    ("Portworx Data Services", re.compile(r"\bpds\b|\bportworx\s+data\s+services\b", re.I)),
]

_PAT_COMPONENTS = [
    ("NVMe/TCP",     re.compile(r"\bnvme\s*/\s*tcp\b", re.I)),
    ("CSI Driver",   re.compile(r"\bcsi\b", re.I)),
    ("Purity",       re.compile(r"\bpurity\b", re.I)),
]

def _heuristic(text: str) -> Dict[str, Any]:
    t = text or ""
    intent = "unknown"
    for name, pat in _PAT_INTENTS:
        if pat.search(t):
            intent = name; break
    entities = {}
    for name, pat in _PAT_PRODUCTS:
        if pat.search(t):
            entities["product"] = name; break
    for name, pat in _PAT_COMPONENTS:
        if pat.search(t):
            entities["component"] = name; break
    mver = re.search(r"\b(?:v|version)\s*([0-9]+(?:\.[0-9]+)*)", t, re.I)
    if mver: entities["version"] = mver.group(1)
    clar = []
    if intent == "unknown":
        clar.append("Do you want a quick overview, how‑to, troubleshooting, or a comparison?")
    if "product" not in entities:
        clar.append("Which product—Portworx, FlashArray, or FlashBlade?")
    return {"intent": intent, "entities": entities, "confidence": 0.6 if intent != "unknown" else 0.3,
            "clarifying_questions": clar}

_INTENT_SYSTEM = (
    "Classify the user's request about Pure Storage. Return JSON with fields: "
    "{intent: 'info|how_to|troubleshoot|compare|design|upgrade|unknown', "
    "entities: {product?, component?, version?}, confidence: number 0..1, "
    "clarifying_questions: array<string> (only if ambiguous). "
    "Be concise. If you are unsure, use intent='unknown' and include 1-2 clarifying_questions."
)

def classify_intent(text: str) -> Dict[str, Any]:
    """Return a dict with intent/entities/confidence/clarifying_questions."""
    t = (text or '').strip()
    if not t:
        return {"intent":"unknown","entities":{},"confidence":0.0,"clarifying_questions":["What should we cover?"]}
    if _OPENAI_OK:
        try:
            resp = _client.chat.completions.create(
                model=os.getenv("OPENAI_INTENT_MODEL", os.getenv("OPENAI_MODEL","gpt-4o-mini")),
                response_format={"type":"json_object"},
                messages=[
                    {"role":"system","content":_INTENT_SYSTEM},
                    {"role":"user","content":t},
                ],
                temperature=0
            )
            # new SDK returns .choices[0].message.content
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)
            # minimal validation
            if "intent" not in data: raise ValueError("no intent")
            if "entities" not in data or not isinstance(data["entities"], dict): data["entities"] = {}
            if "clarifying_questions" not in data: data["clarifying_questions"] = []
            if "confidence" not in data: data["confidence"] = 0.5
            return data
        except Exception:
            # fall back to heuristic
            pass
    return _heuristic(t)
