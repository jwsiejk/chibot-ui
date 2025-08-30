
# services/folksy.py
"""
Nebraska 'farmer-isms' injector for Chip.
- Adds at most one short, fitting Nebraska-style aphorism to the end of a reply.
- Gated by content and probability to avoid overuse.
"""
from __future__ import annotations
import re, hashlib, os
from typing import Optional, Tuple, List

# Curated, neutral one-liners that work for technical conversations.
_FOLKSY: List[str] = [
    "When stuff works, we all sleep better.",
    "Start small, then scale like you mean it.",
    "You shouldn’t need a weather report to know which way the wind’s blowin’.",
    "If it’s simple, it’s stable — that’s just good ranch sense.",
    "Measure twice, deploy once.",
    "Less drama, more uptime.",
    "Keep the herd moving the same direction — consistency beats cleverness.",
    "Strong fences, fewer surprises — guardrails matter.",
    "Don’t outrun your headlights — land the basics first.",
]

# Soft blockers where a folksy tag would feel out of place
_BLOCK_PATTERNS = re.compile(
    r"(\b(error|failed|deprecated|security incident|P0|data loss)\b|```|\bETA\b|^\s*(no|not)\b|\?)",
    flags=re.IGNORECASE,
)

def _should_inject(base_text: str, user_text: str, max_len: int = 180) -> bool:
    rate = None
    try:
        rate = float(os.getenv('CHIP_FOLKSY_RATE','0.2'))
        if rate < 0: rate = 0.0
        if rate > 1: rate = 1.0
    except Exception:
        rate = 0.2
    # Avoid very short or very long answers
    words = len(re.findall(r"\w+", base_text))
    if words < 8 or words > max_len:
        return False
    # Avoid blocked contexts
    if _BLOCK_PATTERNS.search(base_text):
        return False
    # Stable pseudo-random gate: 1 in 3 replies on average
    h = hashlib.md5((base_text + "::" + user_text).encode("utf-8")).hexdigest()
    gate = int(h[:2], 16) / 255.0
    return gate < rate

def inject(text: str, user_text: str) -> Tuple[str, Optional[str]]:
    """
    Maybe append a single folksy closer to the reply.
    Returns (possibly_modified_text, used_quote_or_None).
    """
    if not _should_inject(text, user_text):
        return text, None
    # Choose a quote deterministically from hash for stability
    h = hashlib.md5((text + "||" + user_text).encode("utf-8")).hexdigest()
    idx = int(h[2:4], 16) % len(_FOLKSY)
    q = _FOLKSY[idx]
    # Ensure we don't duplicate if something similar already present
    if q.lower().rstrip(".") in text.lower():
        return text, None
    # Append with an em dash spacer
    sep = " — " if not text.endswith(("!", "?", ".")) else " "
    return f"{text}{sep}{q}", q
