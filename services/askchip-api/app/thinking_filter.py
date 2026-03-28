from __future__ import annotations

import re

THINK_OPEN_TAG = '<think>'
THINK_CLOSE_TAG = '</think>'


class ThinkingLeakFilter:
    """Stateful turn-level filter that strips leaked reasoning traces from streamed content."""

    def __init__(self) -> None:
        self._buffer = ''
        self._in_think_block = False
        self._emitted_chars = 0
        self.leak_filtered = False

    def filter_delta(self, delta_text: str, *, done: bool) -> str:
        if not delta_text and not done:
            return ''

        self._buffer += delta_text
        sanitized = self._strip_thinking_blocks(self._buffer, done=done)
        if self._emitted_chars >= len(sanitized):
            return ''

        output = sanitized[self._emitted_chars:]
        self._emitted_chars = len(sanitized)
        return output

    def _strip_thinking_blocks(self, text: str, *, done: bool) -> str:
        working = text
        leaked_prefix = self._strip_leaked_prefix_before_closing_tag(working)
        if leaked_prefix is not working:
            self.leak_filtered = True
            working = leaked_prefix

        cleaned, in_think = self._remove_complete_and_partial_think_blocks(working, done=done)
        self._in_think_block = in_think
        return cleaned

    @staticmethod
    def _strip_leaked_prefix_before_closing_tag(text: str) -> str:
        lower = text.lower()
        close_index = lower.find(THINK_CLOSE_TAG)
        if close_index < 0:
            return text

        open_index = lower.find(THINK_OPEN_TAG)
        if open_index == -1 or close_index < open_index:
            return text[close_index + len(THINK_CLOSE_TAG):]
        return text

    def _remove_complete_and_partial_think_blocks(self, text: str, *, done: bool) -> tuple[str, bool]:
        complete = re.sub(r'<think>.*?</think>', self._mark_filtered, text, flags=re.IGNORECASE | re.DOTALL)
        if done:
            complete = re.sub(r'<think>.*$', self._mark_filtered, complete, flags=re.IGNORECASE | re.DOTALL)
            complete = re.sub(r'</think>', self._mark_filtered, complete, flags=re.IGNORECASE)
            return complete, False

        open_match = re.search(r'<think>', complete, flags=re.IGNORECASE)
        if open_match:
            self.leak_filtered = True
            return complete[:open_match.start()], True
        return complete, False

    def _mark_filtered(self, match: re.Match[str]) -> str:
        self.leak_filtered = True
        return ''

