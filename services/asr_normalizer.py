
# services/asr_normalizer.py
# Purpose: post-process ASR text to reliably detect Pure Storage product names
# and fix common mis-hearings (e.g., "flash light" -> "FlashBlade", "port works" -> "Portworx").
from __future__ import annotations
import re
import difflib
from typing import Dict, Tuple

# Canonical forms
_CANON = {
    "flashblade": "FlashBlade",
    "flash array": "FlashArray",
    "flasharray": "FlashArray",
    "portworx": "Portworx",
    "pure1": "Pure1",
    "evergreen": "Evergreen",
    "evergreen one": "Evergreen//One",
    "safemode": "SafeMode",
    "flashstack": "FlashStack",
    "directflash": "DirectFlash",
    "nvme-of": "NVMe-oF",
    "nvmeof": "NVMe-oF",
    "air": "AIR",
}

# Very common ASR mis-hearings mapped to the right product
_MISHEARS = {
    r"\bport\s*works?\b": "Portworx",
    r"\bport\s*w(ou|o)rks?\b": "Portworx",
    r"\bpork\s*works?\b": "Portworx",
    r"\bportwor ks\b": "Portworx",
    r"\bflash\s*light\b": "FlashBlade",
    r"\bflash\s*blade(s)?\b": "FlashBlade",
    r"\bflash\s*player\b": "FlashBlade",
    r"\bflash\s*play\b": "FlashBlade",
    r"\bflash\s*bl(ade|eight)\b": "FlashBlade",
    r"\bflash\s*array(s)?\b": "FlashArray",
    r"\bflash\s*ray\b": "FlashArray",
    r"\bpure\s*one\b": "Pure1",
    r"\b(ever green|ever-green)\b": "Evergreen",
    r"\bsafe\s*mode\b": "SafeMode",
    r"\bflash\s*stack\b": "FlashStack",
    r"\bdirect\s*flash\b": "DirectFlash",
    r"\bnvme\s*o(\s*|\-)?f\b": "NVMe-oF",
}

def _apply_regex_map(text: str) -> Tuple[str, Dict[str, str]]:
    changes: Dict[str, str] = {}
    s = text
    for pattern, repl in _MISHEARS.items():
        # find all matches to record changes
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            orig = m.group(0)
            if orig and orig.lower() != repl.lower():
                changes[orig] = repl
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    return s, changes

def _apply_fuzzy(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Light fuzzy correction: for each token and 2-gram window, if it is close to a canonical key,
    replace with the canonical display form. Keeps it conservative to avoid over-correction.
    """
    words = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
    s_lower = text.lower()
    changes: Dict[str, str] = {}

    def best_match(fragment: str):
        frag = fragment.lower().strip()
        best = None
        best_score = 0.0
        for key, display in _CANON.items():
            score = difflib.SequenceMatcher(None, frag, key).ratio()
            if score > best_score:
                best_score, best = score, display
        if best_score >= 0.86:
            return best
        return None

    # 1-gram
    for i, tok in enumerate(words):
        if not tok.isalpha() or len(tok) < 4:
            continue
        repl = best_match(tok)
        if repl and repl.lower() != tok.lower():
            changes[tok] = repl
            words[i] = repl

    # 2-gram (join tokens i and i+1 if both alpha)
    for i in range(len(words)-1):
        a, b = words[i], words[i+1]
        if not (a.isalpha() and b.isalpha()):
            continue
        join = (a + " " + b).lower()
        repl = best_match(join)
        if repl:
            changes[a + " " + b] = repl
            words[i] = repl
            words[i+1] = ""

    # rebuild
    fixed = " ".join([w for w in words if w != ""])
    # Clean double spaces
    fixed = re.sub(r"\s{2,}", " ", fixed).strip()
    return fixed, changes

def normalize_asr(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Return (fixed_text, changes_dict). Safe to run multiple times.
    """
    if not text or not text.strip():
        return text, {}
    s, changes1 = _apply_regex_map(text)
    s2, changes2 = _apply_fuzzy(s)
    changes = {}
    for k, v in {**changes1, **changes2}.items():
        if k and v and k.lower() != v.lower():
            changes[k] = v
    return s2, changes
