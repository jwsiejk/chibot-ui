from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ReasoningMode = Literal['auto_normal', 'auto_think', 'forced_think']

_FORCE_TOKEN_RE = re.compile(r'(?<!\S)/(think|deep)\b', re.IGNORECASE)
_WHITESPACE_RE = re.compile(r'\s+')

_FORCED_PHRASES = (
    'go deeper',
    'think this through',
    'walk me through the tradeoffs',
    'walk me through the trade-offs',
    'walk me through tradeoffs',
    'walk me through trade-offs',
)

_NORMAL_SHORT_PATTERNS = (
    'hi',
    'hello',
    'hey',
    'thanks',
    'thank you',
    'cool',
    'ok',
    'okay',
    'sounds good',
)

_AUTO_THINK_KEYWORDS = (
    'debug',
    'debugging',
    'troubleshoot',
    'troubleshooting',
    'error',
    'stack trace',
    'exception',
    'compare',
    'comparison',
    'tradeoff',
    'trade-off',
    'pros and cons',
    'architecture',
    'migration',
    'design',
    'plan',
    'strategy',
    'step by step',
    'step-by-step',
    'why does',
    'how do i',
    'how should i',
    'algorithm',
    'proof',
    'equation',
    'derive',
    'pseudocode',
    'pseudo-code',
    'refactor',
    'optimize',
)


@dataclass(frozen=True)
class ReasoningDecision:
    mode: ReasoningMode
    think: bool
    user_text: str


def route_reasoning(raw_text: str) -> ReasoningDecision:
    stripped_tokens = strip_reasoning_override_tokens(raw_text)
    lowered = stripped_tokens.casefold()

    if has_forced_reasoning_override(raw_text):
        return ReasoningDecision(mode='forced_think', think=True, user_text=stripped_tokens)

    if _is_auto_think_candidate(stripped_tokens, lowered):
        return ReasoningDecision(mode='auto_think', think=True, user_text=stripped_tokens)

    return ReasoningDecision(mode='auto_normal', think=False, user_text=stripped_tokens)


def has_forced_reasoning_override(text: str) -> bool:
    if _FORCE_TOKEN_RE.search(text):
        return True
    lowered = text.casefold()
    return any(phrase in lowered for phrase in _FORCED_PHRASES)


def strip_reasoning_override_tokens(text: str) -> str:
    cleaned = _FORCE_TOKEN_RE.sub(' ', text)
    cleaned = _WHITESPACE_RE.sub(' ', cleaned).strip()
    return cleaned


def _is_auto_think_candidate(text: str, lowered: str) -> bool:
    if not text:
        return False

    if len(text) <= 40 and lowered in _NORMAL_SHORT_PATTERNS:
        return False

    if any(keyword in lowered for keyword in _AUTO_THINK_KEYWORDS):
        return True

    if any(symbol in text for symbol in ('```', '{', '}', '=>', '==', ' != ', 'SELECT ', 'def ', 'class ')):
        return True

    if text.count('?') >= 2:
        return True

    words = text.split()
    if len(words) >= 45:
        return True

    constraint_tokens = ('must', 'should', 'need', 'require', 'without', 'include', 'constraint', 'exactly', 'do not', "don't")
    constraint_count = sum(lowered.count(token) for token in constraint_tokens)
    if constraint_count >= 3 and len(words) >= 20:
        return True

    return False
