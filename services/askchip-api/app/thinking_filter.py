from __future__ import annotations

import re

THINK_OPEN_TAG = '<think>'
THINK_CLOSE_TAG = '</think>'

SUSPICIOUS_REASONING_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:okay[,:\s]+(?:i|the\s+user)|alright[,:\s]+i|hmm[,:\s]+i|let me|i need to|i should|i'll|"
    r"first thought\s*:|step\s+\d+|thinking\s*:|analysis\s*:|reasoning\s*:|"
    r"i(?:'m| am)\s+(?:thinking|going to think|going to work|working\s+through)|"
    r"to solve this|i can(?:\'t|not)?\s+share|internal(?:\s+monologue)?|chain of thought)"
    r")",
    flags=re.IGNORECASE,
)
SUSPICIOUS_PLANNER_STRUCTURE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"we\s+just\s+had\s+a\s+quick\s+exchange\b|"
    r"we\s+are\b|"
    r"the\s+user\s+asked\b|"
    r"they(?:\s+are|\u2019re|'re)\s+being\b|"
    r"user\s+started\s+with\b|"
    r"the\s+user\s+just\s+said\b|"
    r"first,\s*acknowledge\b|"
    r"key\s+points(?:\s+to\s+hit)?\s*:|"
    r"avoid\s*:|"
    r"how\s+about\b|"
    r"adjusting\s+tone\s*:|"
    r"response\s+drafted\s*:|"
    r"final\s+check\s*:|"
    r"alternative\s*:|"
    r"final\s+decision\s*:"
    r")",
    flags=re.IGNORECASE,
)
SAFE_RELEASE_MARKER = re.compile(
    r"(?:^|[\n.!?]\s*)\s*(?:final answer|answer|response)\s*:\s*",
    flags=re.IGNORECASE,
)
DRAFTED_RESPONSE_RE = re.compile(
    r"response\s+drafted\s*:\s*[\"\u201c\u2018'](?P<draft>.*?)[\"\u201d\u2019']",
    flags=re.IGNORECASE | re.DOTALL,
)


class ThinkingLeakFilter:
    """Stateful turn-level filter that strips leaked reasoning traces from streamed content."""

    def __init__(self, *, buffer_until_safe: bool = False) -> None:
        self._buffer = ''
        self._emitted_chars = 0
        self._holding_unsafe = False
        self._buffer_until_safe = buffer_until_safe
        self._safe_release_reached = not buffer_until_safe
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
        raw_text = self._buffer
        text = raw_text

        if THINK_CLOSE_TAG in raw_text.lower():
            self.leak_filtered = True
            text = self._tail_after_last_close(raw_text)

        cleaned, has_open_without_close = self._strip_think_blocks(text, done=done)
        if has_open_without_close:
            self.leak_filtered = True
            self._holding_unsafe = True
            if not done:
                return '' if self._buffer_until_safe else cleaned

        drafted_response = self._extract_drafted_response(cleaned)
        if drafted_response:
            self.leak_filtered = True
            self._safe_release_reached = True
            return drafted_response

        if self._is_suspicious_content(cleaned):
            self._holding_unsafe = True

        if self._buffer_until_safe and not self._safe_release_reached:
            safe = self._find_safe_answer_region(cleaned, done=done)
            if safe is None:
                self.leak_filtered = self.leak_filtered or self._holding_unsafe
                return ''
            self._safe_release_reached = True
            self._holding_unsafe = False
            return safe

        if self._holding_unsafe:
            self.leak_filtered = True
            if not done:
                return ''
            safe_tail = self._find_safe_answer_region(cleaned, done=True)
            self._holding_unsafe = False
            return safe_tail or ''

        return cleaned

    @staticmethod
    def _strip_think_blocks(text: str, *, done: bool) -> tuple[str, bool]:
        output: list[str] = []
        cursor = 0
        lower = text.lower()
        has_open_without_close = False

        while cursor < len(text):
            open_index = lower.find(THINK_OPEN_TAG, cursor)
            if open_index == -1:
                output.append(text[cursor:])
                break
            output.append(text[cursor:open_index])
            close_index = lower.find(THINK_CLOSE_TAG, open_index + len(THINK_OPEN_TAG))
            if close_index == -1:
                has_open_without_close = True
                if done:
                    break
                return ''.join(output), True
            cursor = close_index + len(THINK_CLOSE_TAG)

        return ''.join(output), has_open_without_close

    @staticmethod
    def _extract_drafted_response(text: str) -> str | None:
        match = None
        for candidate in DRAFTED_RESPONSE_RE.finditer(text):
            match = candidate
        if match is None:
            return None
        draft = match.group('draft').strip()
        if not draft or ThinkingLeakFilter._is_suspicious_content(draft):
            return None
        return draft

    @staticmethod
    def _is_suspicious_content(text: str) -> bool:
        sample = text.strip()
        if not sample:
            return False
        if sample.startswith('<'):
            return False
        prefix = sample[:320]
        if SUSPICIOUS_REASONING_PREFIX.match(prefix):
            return True
        if SUSPICIOUS_PLANNER_STRUCTURE.search(sample[:1200]):
            return True
        return False

    @staticmethod
    def _find_safe_answer_region(text: str, *, done: bool) -> str | None:
        trimmed = text.strip()
        if not trimmed:
            return None

        marker_match = None
        for match in SAFE_RELEASE_MARKER.finditer(text):
            marker_match = match
        if marker_match is not None:
            tail = text[marker_match.end():].lstrip()
            if tail and not ThinkingLeakFilter._is_suspicious_content(tail):
                return tail

        safe_markers = (
            'here is the actual answer:',
            "here's the actual answer:",
            'here is the answer:',
            "here's the answer:",
            'the answer is:',
            'final answer:',
        )
        lower = text.lower()
        for marker in safe_markers:
            index = lower.rfind(marker)
            if index == -1:
                continue
            tail = text[index + len(marker):].lstrip()
            if tail and not ThinkingLeakFilter._is_suspicious_content(tail):
                return tail

        if not ThinkingLeakFilter._is_suspicious_content(trimmed):
            return text

        if done:
            return None
        return None

    @staticmethod
    def _tail_after_last_close(text: str) -> str:
        lower = text.lower()
        index = lower.rfind(THINK_CLOSE_TAG)
        if index == -1:
            return text
        return text[index + len(THINK_CLOSE_TAG):]
