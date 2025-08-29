# services/context_guard.py
"""
Context & Domain Guard for Chip (Pure Storage)
- Tracks the current Pure Storage topic across turns (FlashBlade, FlashArray, etc.).
- Corrects common mis-mentions (e.g., "flash player" -> "FlashBlade") using heuristics + history.
- Adds a compact context prefix so the LLM stays anchored to Pure and the active product.
- Keeps everything optional and non-breaking for other routes.
"""
from __future__ import annotations
import re
import difflib
from typing import List, Tuple, Optional, Dict, Any

# Canonical Pure products (extend as needed)
PURE_PRODUCTS = {
    "flashblade": {"aliases": ["flash blade", "fb", "fb//s", "flashblade//s", "fb-s", "flash-blade"]},
    "flasharray": {"aliases": ["flash array", "fa", "fa-x", "fa//c", "fa//x", "flash-array"]},
    "portworx": {"aliases": ["px", "px-enterprise", "port works"]},
    "pure1": {"aliases": ["pure 1", "pureone", "pure-one"]},
    "air": {"aliases": ["ai copilot", "pure ai", "pure air", "copilot"]},
    "evergreen": {"aliases": ["ever green", "evergreen//one", "evergreen one", "evergreen//flex"]},
    "safemode": {"aliases": ["safe mode", "safe-mode"]},
}

# Common non-Pure confusions -> map back to Pure when history suggests it
MISNOMERS = {
    # lowercased misnomer : corrected product key
    "flash player": "flashblade",
    "adobe flash": "flashblade",
    "shockwave": "flashblade",
}

# Lightweight product extraction
def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def detect_product(text: str) -> Optional[str]:
    t = _normalize(text)
    for prod, meta in PURE_PRODUCTS.items():
        if prod in t:
            return prod
        for alias in meta.get("aliases", []):
            if alias in t:
                return prod
    # fuzzy fallback
    choices = [p for p in PURE_PRODUCTS.keys()]
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9/\\-]+", t):
        m = difflib.get_close_matches(w.lower(), choices, n=1, cutoff=0.86)
        if m:
            return m[0]
    return None

def extract_products_from_history(history: List[Dict[str, Any]]) -> List[str]:
    found: List[str] = []
    for msg in history or []:
        content = _normalize(str(msg.get("content") or msg.get("text") or ""))
        p = detect_product(content)
        if p and (not found or found[-1] != p):
            found.append(p)
    return found

def is_generic_install_request(text: str) -> bool:
    t = _normalize(text)
    return (
        ("install" in t or "installation" in t or "set up" in t or "setup" in t)
        and not any(k in t for k in ["flashblade", "flash array", "flasharray", "portworx", "pure1", "evergreen", "safemode"])
    )

def correct_misnomers(text: str, current_product: Optional[str], history_products: List[str]) -> Tuple[str, Optional[str]]:
    t = _normalize(text)
    # Explicit misnomers
    for bad, prod in MISNOMERS.items():
        if bad in t:
            # If history suggests the target product or no other product is present, correct
            if current_product == prod or prod in history_products or current_product is None:
                corrected = re.sub(bad, prod, t)
                # Rebuild with capitalization for surface form
                surface = "FlashBlade" if prod == "flashblade" else prod.title()
                fixed = re.sub(bad, surface, text, flags=re.IGNORECASE)
                return fixed, prod
    return text, None

def build_context_prefix(product: Optional[str], text: str) -> str:
    """Return a short prefix that anchors the LLM without sounding like a system dump."""
    if not product:
        return ""
    # Brief, natural anchor; not a numbered list; nudges Pure and install intent
    if is_generic_install_request(text):
        return f"(Context: We’re discussing Pure Storage {product.title()}. The user is asking for installation steps. Keep it short and on Pure.)\n"
    return f"(Context: We’re discussing Pure Storage {product.title()}. Keep replies anchored to this product.)\n"

def resolve_context(text: str, history: Optional[List[dict]] = None, session_topic: Optional[str] = None) -> Dict[str, Any]:
    """Infer the most likely product/topic and return corrections & prefix."""
    history = history or []
    hist_products = extract_products_from_history(history)
    detected_now = detect_product(text)
    product = detected_now or session_topic or (hist_products[-1] if hist_products else None)

    # Correct misnomers (e.g., "flash player")
    fixed_text, corrected_to = correct_misnomers(text, product, hist_products)
    if corrected_to and not product:
        product = corrected_to

    # If user asked generic install and we have a product in context, keep it
    prefix = build_context_prefix(product, fixed_text)

    return {
        "product": product,
        "fixed_text": fixed_text,
        "prefix": prefix,
    }
