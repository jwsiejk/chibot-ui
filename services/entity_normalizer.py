# services/entity_normalizer.py
from __future__ import annotations
import re
from typing import Optional, Tuple, Dict, List
from .context_guard import resolve_context as _resolve, detect_product as _detect_raw

# Canonical to display
_DISPLAY = {
    "flashblade": "FlashBlade",
    "flasharray": "FlashArray",
    "portworx": "Portworx",
    "pure1": "Pure1",
    "safemode": "SafeMode",
    "evergreen": "Evergreen",
    "flashstack": "FlashStack",
    "air": "AIR",
}

_INTENT_PATTERNS = {
    "install": r"\b(install|set\s*up|setup|deploy|bring\s*up|rack|cable)\b",
    "configure": r"\b(config(ure|uring)?|set\s*tings?|tune|policy|snapshot|replication|network)\b",
    "troubleshoot": r"\b(troubleshoot|debug|issue|problem|error|fail|down)\b",
    "upgrade": r"\b(upgrade|update|patch)\b",
    "design": r"\b(design|arch(itecture)?|size|sizing|capacity|plan)\b",
}

def detect_intent(text: str) -> Optional[str]:
    s = (text or "").lower()
    for k, pat in _INTENT_PATTERNS.items():
        if re.search(pat, s):
            return k
    return None

def detect_product(text: str) -> Optional[str]:
    p = _detect_raw(text)
    return _DISPLAY.get(p or "", None)

def normalize_text_to_pure(text: str, preferred_product: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """
    Returns (normalized_text, updates)
    - Fixes common misnomers like 'flash player' or 'flash play' -> FlashBlade
    - If preferred_product is given, nudges ambiguous 'flash' references to that product
    """
    # Use context_guard to resolve and correct misnomers
    ctx = _resolve(text, history=[], session_topic=(preferred_product or None))
    fixed = ctx.get("fixed_text") or text
    prod_key = ctx.get("product")
    updates: Dict[str, str] = {}
    if prod_key:
        updates["product"] = _DISPLAY.get(prod_key, prod_key.title())
    # Gentle normalization: standardize casing for canonical names
    fixed = re.sub(r"\bflash\s*-?\s*blade(s)?\b", "FlashBlade", fixed, flags=re.I)
    fixed = re.sub(r"\bflash\s*-?\s*array(s)?\b", "FlashArray", fixed, flags=re.I)
    return fixed, updates
