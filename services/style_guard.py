# services/style_guard.py
"""
Style Guard for Chip (Pure Storage VSE)
- Enforces conversational tone and short, flowing answers.
- Rewrites numbered/bulleted lists into inline steps with varied connectors.
- Optional Pure tie-back nudges are handled in the prompt; this guard keeps output tight and list-free.
"""
from __future__ import annotations
import re
import random
from typing import List, Tuple

# Configurable limits
DEFAULT_MAX_WORDS = 30  # default upper bound for short answers
HARD_MAX_WORDS = 60    # absolute clip (failsafe)

# Patterns that indicate rigid/numbered lists
_LIST_PATTERNS = [
    r"^\s*\d+[\.)]\s+",          # 1.  1)
    r"^\s*[a-zA-Z]\)\s+",         # a) b) c)
    r"^\s*[-•]\s+",                # -  •
]

_LIST_RE = re.compile("|".join(_LIST_PATTERNS), re.MULTILINE)

_CONNECTOR_SETS = [
    ["First,", "Next,", "Then,", "Finally,"],
    ["To start,", "After that,", "From there,", "Lastly,"],
    ["Begin with", "Next up,", "Afterward,", "To wrap up,"],
    ["Start with", "Continue with", "Then,", "In the end,"],
]

def _split_list_lines(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    numbered = []
    for ln in lines:
        m = re.match(r"^(?:\d+[\.)]|[a-zA-Z]\)|[-•])\s+(.*)$", ln)
        if m:
            numbered.append(m.group(1).strip())
        else:
            # If any line doesn't match, return empty => treat as non-list
            return []
    return numbered

def _words(text: str) -> List[str]:
    return re.findall(r"[\w'’\-]+", text)

def _truncate(text: str, max_words: int) -> str:
    toks = _words(text)
    if len(toks) <= max_words:
        return text
    # truncate at word boundary
    cut = max_words
    keep = " ".join(toks[:cut])
    return keep + "…"

def _choose_connectors(n: int) -> List[str]:
    # Choose one set; if more items than connectors, cycle with slight variants
    base = random.choice(_CONNECTOR_SETS)
    out = []
    for i in range(n):
        c = base[min(i, len(base)-1)]
        # Light variation
        if i >= len(base)-1:
            c = c.replace(",", "")  # remove comma occasionally
        out.append(c)
    return out

def _rewrite_list_to_inline(items: List[str]) -> str:
    connectors = _choose_connectors(len(items))
    parts = []
    for i, it in enumerate(items):
        # Ensure item starts lowercase unless it’s a proper noun
        it = it[0].lower() + it[1:] if it and it[0].isalpha() else it
        # Add comma if missing
        if not it.endswith((".", "!", "?")):
            it = it + "."
        piece = f"{connectors[i]} {it}"
        parts.append(piece)
    # Reduce over-punctuation by joining with space
    return " ".join(parts)

def enforce(text: str, max_words: int = DEFAULT_MAX_WORDS) -> Tuple[str, List[str]]:
    """
    Returns (fixed_text, issues)
    - Removes rigid lists
    - Rewrites into flowing steps when needed
    - Truncates to max_words (soft) and HARD_MAX_WORDS (hard)
    """
    issues: List[str] = []
    if not text or not isinstance(text, str):
        return text, issues

    # Strip leading/trailing whitespace
    txt = text.strip()

    # 1) Detect and rewrite list blocks
    if _LIST_RE.search(txt):
        items = _split_list_lines(txt)
        if items:
            txt = _rewrite_list_to_inline(items)
            issues.append("rewrote_list_to_inline")
        else:
            # If it had numbering markers but wasn't a clean list, just remove markers
            txt = re.sub(r"^(?:\d+[\.)]|[a-zA-Z]\)|[-•])\s+", "", txt, flags=re.MULTILINE)
            txt = re.sub(r"\n+", " ", txt).strip()
            issues.append("stripped_list_markers")

    # 2) Collapse excessive newlines
    if "\n" in txt:
        txt = re.sub(r"\s*\n\s*", " ", txt).strip()

    # 3) Trim verbosity
    txt_soft = _truncate(txt, max_words)
    if txt_soft != txt:
        issues.append("soft_truncated")
        txt = txt_soft

    # 4) Hard cap in case model went long
    if len(_words(txt)) > HARD_MAX_WORDS:
        txt = _truncate(txt, HARD_MAX_WORDS)
        issues.append("hard_truncated")

    # 5) Clean double spaces
    txt = re.sub(r"\s{2,}", " ", txt).strip()

    return txt, issues
