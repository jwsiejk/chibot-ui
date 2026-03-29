from __future__ import annotations

import re

THINK_OPEN_TAG = '<think>'
THINK_CLOSE_TAG = '</think>'

SUSPICIOUS_REASONING_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:okay[,:\s]+(?:i|the\s+user)|alright[,:\s]+i|hmm[,:\s]+i|let me|i need to|i should|i'll|first(?:,|\s)|step\s+\d+|thinking\s*:|analysis\s*:|reasoning\s*:|"
    r"i(?:'m| am)\s+(?:thinking|going to think|going to work|working\s+through)|"
    r"to solve this|i can(?:\'t|not)?\s+share|internal(?:\s+monologue)?|chain of thought)"
    r")",
    flags=re.IGNORECASE,
)
SUSPICIOUS_PLANNER_STRUCTURE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"we are\b|"
    r"user started with\b|"
    r"the user just said\b|"
    r"first,\s*acknowledge\b|"
    r"key points:\s*|"
    r"alternative:\s*|"
    r"final decision:\s*"
    r")",
    flags=re.IGNORECASE,
)
SAFE_RELEASE_MARKER = re.compile(
    r"(?:^|[\n.!?]\s*)\s*(?:final answer|answer|response)\s*:\s*",
    flags=re.IGNORECASE,
)


class ThinkingLeakFilter:
    """Stateful turn-level filter that strips leaked reasoning traces from streamed content."""

    def __init__(self) -> None:
        self._buffer = ''
        self._emitted_chars = 0
        self._holding_unsafe = False
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
        lower_text = text.lower()

        if saw_unmatched_close:
            self.leak_filtered = True
            safe_tail = self._sanitize_safe_tail(self._tail_after_last_close(text), done=done)
            if safe_tail:
                self._holding_unsafe = False
            return safe_tail

        if has_open_without_close:
            self.leak_filtered = True
            self._holding_unsafe = True
            if done:
                return self._sanitize_safe_tail(cleaned, done=True)
            return ''

        if self._is_suspicious_content(cleaned):
            self._holding_unsafe = True

        if self._holding_unsafe and THINK_CLOSE_TAG in lower_text:
            self.leak_filtered = True
            safe_tail = self._sanitize_safe_tail(self._tail_after_last_close(text), done=done)
            if safe_tail:
                self._holding_unsafe = False
            return safe_tail

        if self._holding_unsafe:
            self.leak_filtered = True
            if not done:
                return ''
            safe_tail = self._sanitize_safe_tail(cleaned, done=True)
            self._holding_unsafe = False
            return safe_tail

        if done:
            self._holding_unsafe = False

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
    def _is_suspicious_content(text: str) -> bool:
        sample = text.strip()
        if not sample:
            return False
        if sample.startswith('<'):
            return False
        if THINK_CLOSE_TAG in sample.lower():
            return True
        prefix = sample[:220]
        if SUSPICIOUS_REASONING_PREFIX.match(prefix):
            return True
        if SUSPICIOUS_PLANNER_STRUCTURE.search(sample[:600]):
            return True
        return False

    @staticmethod
    def _sanitize_safe_tail(text: str, *, done: bool) -> str:
        trimmed = text.strip()
        if not trimmed:
            return ''

        if not ThinkingLeakFilter._is_suspicious_content(trimmed):
            return text

        marker_match = None
        for match in SAFE_RELEASE_MARKER.finditer(text):
            marker_match = match
        if marker_match is not None:
            tail = text[marker_match.end():]
            if tail.strip() and not ThinkingLeakFilter._is_suspicious_content(tail.strip()):
                return tail.lstrip()

        safe_markers = (
            'here is the actual answer:',
            "here's the actual answer:",
            'here is the answer:',
            "here's the answer:",
            'the answer is:',
            'here is the actual answer',
            "here's the actual answer",
            'here is the answer',
            "here's the answer",
            'the answer is',
            '\n\n',
        )
        lower = text.lower()
        for marker in safe_markers:
            index = lower.rfind(marker)
            if index == -1:
                continue
            start = index + len(marker)
            tail = text[start:]
            if tail.strip() and not ThinkingLeakFilter._is_suspicious_content(tail.strip()):
                return tail.lstrip()

        if done:
            return ''
        return ''

    @staticmethod
    def _tail_after_last_close(text: str) -> str:
        lower = text.lower()
        index = lower.rfind(THINK_CLOSE_TAG)
        if index == -1:
            return text
        return text[index + len(THINK_CLOSE_TAG):]
