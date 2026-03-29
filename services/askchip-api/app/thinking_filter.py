from __future__ import annotations

import re

THINK_OPEN_TAG = '<think>'
THINK_CLOSE_TAG = '</think>'

SUSPICIOUS_REASONING_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:okay[,:\s]+i|alright[,:\s]+i|hmm[,:\s]+i|let me|i need to|i should|i'll|first(?:,|\s)|step\s+\d+|thinking\s*:|analysis\s*:|reasoning\s*:|"
    r"i(?:'m| am)\s+(?:thinking|going to think|going to work|working\s+through)|"
    r"to solve this|i can(?:\'t|not)?\s+share|internal(?:\s+monologue)?|chain of thought)"
    r")",
    flags=re.IGNORECASE,
)


class ThinkingLeakFilter:
    """Stateful turn-level filter that strips leaked reasoning traces from streamed content."""

    def __init__(self) -> None:
        self._buffer = ''
        self._emitted_chars = 0
        self._holding_suspicious_prefix = False
        self.leak_filtered = False

    def filter_delta(self, delta_text: str, *, done: bool) -> str:
        if not delta_text and not done:
            return ''

        self._buffer += delta_text
        sanitized = self._sanitize_stream(done=done)
        if self._emitted_chars >= len(sanitized):
            return ''

        output = sanitized[self._emitted_chars:]
        self._emitted_chars = len(sanitized)
        return output

    def _sanitize_stream(self, *, done: bool) -> str:
        text = self._buffer
        cleaned, saw_unmatched_close, has_open_without_close = self._strip_think_markup(text, done=done)

        if saw_unmatched_close:
            self.leak_filtered = True
            self._holding_suspicious_prefix = False
            return cleaned

        if has_open_without_close:
            self.leak_filtered = True
            self._holding_suspicious_prefix = True
            if done:
                self._holding_suspicious_prefix = False
                return cleaned
            return cleaned

        if self._is_suspicious_leading_prefix(text):
            self._holding_suspicious_prefix = True

        if self._holding_suspicious_prefix and THINK_CLOSE_TAG not in text.lower():
            self.leak_filtered = True
            if not done:
                return ''
            self._holding_suspicious_prefix = False
            return self._extract_safe_tail_from_suspicious_done(cleaned)

        if done:
            self._holding_suspicious_prefix = False

        return cleaned

    def _strip_think_markup(self, text: str, *, done: bool) -> tuple[str, bool, bool]:
        output: list[str] = []
        cursor = 0
        lower = text.lower()
        in_think_block = False
        saw_unmatched_close = False
        has_open_without_close = False

        while cursor < len(text):
            if not in_think_block:
                next_open = lower.find(THINK_OPEN_TAG, cursor)
                next_close = lower.find(THINK_CLOSE_TAG, cursor)

                if next_close != -1 and (next_open == -1 or next_close < next_open):
                    saw_unmatched_close = True
                    output.clear()
                    cursor = next_close + len(THINK_CLOSE_TAG)
                    continue

                if next_open == -1:
                    output.append(text[cursor:])
                    break

                output.append(text[cursor:next_open])
                cursor = next_open + len(THINK_OPEN_TAG)
                in_think_block = True
                self.leak_filtered = True
                continue

            close_index = lower.find(THINK_CLOSE_TAG, cursor)
            if close_index == -1:
                has_open_without_close = True
                if done:
                    cursor = len(text)
                break

            cursor = close_index + len(THINK_CLOSE_TAG)
            in_think_block = False

        return ''.join(output), saw_unmatched_close, has_open_without_close

    @staticmethod
    def _is_suspicious_leading_prefix(text: str) -> bool:
        sample = text.strip()
        if not sample:
            return False
        if sample.startswith('<'):
            return False
        if THINK_CLOSE_TAG in sample.lower():
            return True
        prefix = sample[:140]
        return bool(SUSPICIOUS_REASONING_PREFIX.match(prefix))

    @staticmethod
    def _extract_safe_tail_from_suspicious_done(text: str) -> str:
        trimmed = text.strip()
        if not trimmed:
            return ''

        safe_markers = (
            'final answer:',
            'answer:',
            'response:',
            '\n\n',
        )
        lower = text.lower()
        for marker in safe_markers:
            index = lower.rfind(marker)
            if index == -1:
                continue
            start = index + len(marker)
            tail = text[start:].strip()
            if tail and not ThinkingLeakFilter._is_suspicious_leading_prefix(tail):
                return tail

        return ''
