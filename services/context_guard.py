# services/context_guard.py
"""
Context & Domain Guard for Chip (Pure Storage)
- Detects the current Pure Storage product.
- Corrects common ASR mis-hearings (e.g., "flash light" → FlashBlade, "port works" → Portworx).
- Builds a compact context prefix so the LLM stays anchored to Pure and the active product.
"""
from __future__ import annotations
import re
import difflib
from typing import List, Tuple, Optional, Dict, Any

# Canonical product keys and display forms
PURE_PRODUCTS: Dict[str, Dict[str, Any]] = {
    "flashblade": {
        "aliases": [
            "flash blade", "flash blades", "flashblade", "flash light", "flashlight",
            "flash late", "flash play", "flash player"
        ],
        "display": "FlashBlade",
    },
    "flashblade//s": {
        "aliases": ["flash blade s", "fb-s", "fbs", "flashblade//s", "flashblade/s"],
        "display": "FlashBlade//S",
    },
    "flasharray": {
        "aliases": ["flash array", "flash arrays", "flashray"],
        "display": "FlashArray",
    },
    "portworx": {
        "aliases": ["portworx", "port works", "portworks", "port woks", "port work"],
        "display": "Portworx",
    },
    "pure1": { "aliases": ["pure1", "pure one", "pure-1"], "display": "Pure1" },
    "safemode": { "aliases": ["safemode", "safe mode", "safe-mode"], "display": "SafeMode" },
    "evergreen": { "aliases": ["evergreen", "ever green"], "display": "Evergreen" },
    "flashstack": { "aliases": ["flashstack", "flash stack"], "display": "FlashStack" },
    "air": { "aliases": ["air", "a i r", "ai r"], "display": "AIR" },
}

CANONICAL_KEYS: List[str] = list(PURE_PRODUCTS.keys())

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _closest_key(text: str) -> Optional[str]:
    t = _norm(text)
    # direct alias hits
    for key, meta in PURE_PRODUCTS.items():
        for alias in meta["aliases"]:
            if alias in t:
                return key
    # fuzzy token/bigram search
    tokens = re.findall(r"[a-z0-9//\-]+", t)
    cands = set(tokens)
    words = re.findall(r"[a-z0-9]+", t)
    for i in range(len(words)-1):
        cands.add(words[i] + " " + words[i+1])
        if i < len(words)-2:
            cands.add(words[i] + " " + words[i+1] + " " + words[i+2])
    best = None
    best_score = 0.0
    choices = CANONICAL_KEYS + [PURE_PRODUCTS[k]["display"].lower() for k in CANONICAL_KEYS]
    for cand in cands:
        match = difflib.get_close_matches(cand.lower(), choices, n=1, cutoff=0.83)
        if match:
            m = match[0]
            # map to key
            for k in CANONICAL_KEYS:
                if m == k or m == PURE_PRODUCTS[k]["display"].lower():
                    score = min(1.0, 0.8 + len(cand)/20.0)
                    if score > best_score:
                        best_score = score
                        best = k
    return best

def detect_product(text: str) -> Optional[str]:
    key = _closest_key(text)
    return key

def _apply_casing(s: str) -> str:
    out = s
    for k, meta in PURE_PRODUCTS.items():
        disp = meta["display"]
        # Ensure proper casing for known product families
        if k.startswith("flashblade"):
            out = re.sub(r"\bflash\s*-?\s*blade(s)?\b", "FlashBlade", out, flags=re.I)
            out = re.sub(r"\bflashblade//?s\b", "FlashBlade//S", out, flags=re.I)
        elif k == "flasharray":
            out = re.sub(r"\bflash\s*-?\s*array(s)?\b", "FlashArray", out, flags=re.I)
        elif k == "portworx":
            out = re.sub(r"\bport\s*-?\s*worx\b", "Portworx", out, flags=re.I)
        elif k == "pure1":
            out = re.sub(r"\bpure\s*-?\s*1\b", "Pure1", out, flags=re.I)
        elif k == "safemode":
            out = re.sub(r"\bsafe\s*-?\s*mode\b", "SafeMode", out, flags=re.I)
        elif k == "flashstack":
            out = re.sub(r"\bflash\s*-?\s*stack\b", "FlashStack", out, flags=re.I)
        elif k == "air":
            out = re.sub(r"\bair\b", "AIR", out, flags=re.I)
        # alias normalization
        for alias in meta["aliases"]:
            out = re.sub(r"\b" + re.escape(alias) + r"\b", disp, out, flags=re.I)
    return out

def correct_misnomers(text: str, product: Optional[str], history_products: List[str]) -> Tuple[str, Optional[str]]:
    fixed = _apply_casing(text)
    corrected_to = None
    # If generic "flash" appears and we have context, bias to that
    if re.search(r"\bflash\b", text, flags=re.I):
        if product:
            corrected_to = product
        elif history_products:
            corrected_to = history_products[-1]
    # Otherwise, if we can detect a product from text, use that
    if not corrected_to:
        guess = detect_product(text)
        if guess:
            corrected_to = guess
    return fixed, corrected_to

def build_context_prefix(product_key: Optional[str], text: str) -> str:
    if not product_key:
        return ""
    disp = PURE_PRODUCTS.get(product_key, {}).get("display") or product_key.title()
    return f"[Pure topic: {disp}] "

def resolve_context(text: str,
                    history: List[Dict[str, str]] | List[str] | None = None,
                    session_topic: Optional[str] = None) -> Dict[str, Any]:
    # extract prior product mentions from history
    hist_products: List[str] = []
    try:
        if history and isinstance(history, list):
            for item in history:
                if isinstance(item, dict):
                    p = item.get("product") or item.get("topic") or ""
                    if p:
                        hist_products.append(_norm(p))
                elif isinstance(item, str):
                    key = detect_product(item)
                    if key:
                        hist_products.append(key)
    except Exception:
        pass

    product = session_topic or detect_product(text) or (hist_products[-1] if hist_products else None)

    fixed_text, corrected_to = correct_misnomers(text, product, hist_products)
    if corrected_to and not product:
        product = corrected_to

    prefix = build_context_prefix(product, fixed_text)

    return {"product": product, "fixed_text": fixed_text, "prefix": prefix}
