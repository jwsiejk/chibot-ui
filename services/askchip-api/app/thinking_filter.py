from __future__ import annotations

THINK_OPEN_TAG = '<think>'
THINK_CLOSE_TAG = '</think>'


class ThinkingLeakFilter:
    """Stateful stream filter that strips generic <think>...</think> leakage."""

    def __init__(self) -> None:
        self._buffer = ''
        self._emitted_chars = 0
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
        text, has_open_without_close = self._strip_think_blocks(self._buffer, done=done)
        cleaned_text = text.replace(THINK_CLOSE_TAG, '').replace(THINK_CLOSE_TAG.upper(), '')
        if cleaned_text != text:
            self.leak_filtered = True
        if has_open_without_close:
            self.leak_filtered = True
        return cleaned_text

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
