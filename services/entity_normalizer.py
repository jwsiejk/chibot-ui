# services/entity_normalizer.py
from __future__ import annotations
import re
import difflib
from typing import Optional, Tuple, Dict, List
from .context_guard import resolve_context as _resolve, detect_product as _detect_raw

# Canonical display names
_DISPLAY = {
    "flashblade": "FlashBlade",
    "flashblade//s": "FlashBlade//S",
    "flasharray": "FlashArray",
    "portworx": "Portworx",
    "pure1": "Pure1",
    "safemode": "SafeMode",
    "evergreen": "Evergreen",
    "flashstack": "FlashStack",
    "air": "AIR",
}

# Very common ASR miss-hearings → canonical key
_MISHEAR_MAP: Dict[str, str] = {
    # FlashBlade
    "flash blade": "flashblade",
    "flash blades": "flashblade",
    "flashblade": "flashblade",
    "flash light": "flashblade",
    "flashlight": "flashblade",
    "flash late": "flashblade",
    "flash play": "flashblade",
    "flash player": "flashblade",
    "flash blade s": "flashblade//s",
    "fb-s": "flashblade//s",
    "fbs": "flashblade//s",
    # FlashArray
    "flash array": "flasharray",
    "flash arrays": "flasharray",
    "flashray": "flasharray",
    # Portworx
    "portworks": "portworx",
    "port works": "portworx",
    "port woks": "portworx",
    "port work": "portworx",
    "portworx": "portworx",
    "port works enterprise": "portworx",
    # Pure1
    "pure one": "pure1",
    "pure-1": "pure1",
    # SafeMode
    "safe mode": "safemode",
    "safe-mode": "safemode",
    # Evergreen
    "ever green": "evergreen",
    # FlashStack
    "flash stack": "flashstack",
    # AIR
    "a i r": "air",
    "ai r": "air",
}

_CANONICAL_KEYS: List[str] = list(_DISPLAY.keys())

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _closest_product_token(text: str) -> Optional[str]:
    """
    Try to find the closest Pure product token in the text using:
    1) direct MISHEAR_MAP hits
    2) fuzzy matching against canonical keys and common terms
    """
    t = _norm(text)
    # direct replacements
    for wrong, key in _MISHEAR_MAP.items():
        if wrong in t:
            return key
    # token-level fuzzy search
    tokens = re.findall(r"[a-z0-9//\-]+", t)
    candidates = set(tokens)
    # add bigrams/trigrams for phrases like 'port works'
    words = re.findall(r"[a-z0-9]+", t)
    for i in range(len(words)-1):
        candidates.add(words[i] + " " + words[i+1])
        if i < len(words)-2:
            candidates.add(words[i] + " " + words[i+1] + " " + words[i+2])
    # check fuzzy closeness
    best_key = None
    best_score = 0.0
    for cand in candidates:
        # try direct map first
        if cand in _MISHEAR_MAP:
            return _MISHEAR_MAP[cand]
        # fuzzy against canonical keys and known phrases
        choices = _CANONICAL_KEYS + list(_DISPLAY.values())
        match = difflib.get_close_matches(cand.lower(), choices, n=1, cutoff=0.82)
        if match:
            m = match[0].lower()
            # map display → key
            for k, disp in _DISPLAY.items():
                if m == k or m == disp.lower():
                    # prefer longer matches slightly
                    score = min(1.0, 0.8 + len(cand)/20.0)
                    if score > best_score:
                        best_score = score
                        best_key = k
    return best_key

def detect_product(text: str) -> Optional[str]:
    """
    Return canonical display name if we can infer a product mention.
    """
    # Reuse context_guard when available for consistency
    try:
        raw = _detect_raw(text)  # returns canonical key like 'flashblade'
        if raw:
            return _DISPLAY.get(raw, raw.title())
    except Exception:
        pass
    # Fallback: local heuristic
    key = _closest_product_token(text)
    return _DISPLAY.get(key) if key else None

_INTENTS = {
    "install": ["install", "deploy", "set up", "setup"],
    "configure": ["configure", "config", "tune"],
    "troubleshoot": ["troubleshoot", "fix", "issue", "error", "problem"],
    "design": ["design", "size", "sizing", "architecture"],
    "migrate": ["migrate", "migration", "move data"],
    "protect": ["protect", "backup", "ransomware", "snapshot", "replication"],
}

def detect_intent(text: str) -> Optional[str]:
    t = _norm(text)
    for intent, kws in _INTENTS.items():
        for kw in kws:
            if re.search(r"\b" + re.escape(kw) + r"\b", t):
                return intent
    return None

def _apply_case_and_terms(s: str) -> str:
    """
    Normalize Pure terms casing in the text after we decide on a product.
    """
    # Normalize common product spellings/casing
    fixes = [
        (r"\bflash\s*-?\s*blade(s)?\b", "FlashBlade"),
        (r"\bflash\s*-?\s*array(s)?\b", "FlashArray"),
        (r"\bflashblade//?s\b", "FlashBlade//S"),
        (r"\bport\s*-?\s*worx\b", "Portworx"),
        (r"\bpure\s*-?\s*1\b", "Pure1"),
        (r"\bsafe\s*-?\s*mode\b", "SafeMode"),
        (r"\bflash\s*-?\s*stack\b", "FlashStack"),
        (r"\bair\b", "AIR"),
    ]
    out = s
    for pat, repl in fixes:
        out = re.sub(pat, repl, out, flags=re.I)
    # Map common mishears
    for wrong, key in _MISHEAR_MAP.items():
        disp = _DISPLAY.get(key, key.title())
        out = re.sub(r"\b" + re.escape(wrong) + r"\b", disp, out, flags=re.I)
    return out

def normalize_text_to_pure(text: str, preferred_product: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """Normalize user text toward Pure nomenclature and return (fixed_text, updates).
    - Fixes common mishears like 'flash light' → FlashBlade, 'port works' → Portworx
    - If preferred_product is given, nudges ambiguous 'flash' references to that product
    """
    txt = (text or "")
    # Baseline normalization using context_guard (it also corrects misnomers)
    ctx = _resolve(txt, history=[], session_topic=(preferred_product or None))
    fixed = ctx.get("fixed_text") or txt
    prod_key = ctx.get("product")  # canonical key
    if not prod_key:
        guess = _closest_product_token(fixed)
        if guess:
            prod_key = guess
    # Apply casing + mishear fixes
    fixed = _apply_case_and_terms(fixed)
    updates: Dict[str, str] = {}
    if prod_key:
        updates["product"] = _DISPLAY.get(prod_key, prod_key.title())
    return fixed, updates
