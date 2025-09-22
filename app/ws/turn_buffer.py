# app/ws/turn_buffer.py
"""
In-connection audio turn buffer.
Collects binary frames until CloseStream. Stateless across turns other than seq id.
"""

from typing import List


class TurnBuffer:
    def __init__(self):
        self._buf: List[bytes] = []
        self.turn_seq = 0  # increments at CloseStream

    def append(self, chunk: bytes):
        """Append an audio chunk for the current turn."""
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("binary_only")
        self._buf.append(bytes(chunk))

    def is_empty(self) -> bool:
        """Return True when no audio has been buffered for the current turn."""
        return not self._buf

    def close_turn(self):
        """Increment the turn counter and return (turn_id, joined_bytes)."""
        self.turn_seq += 1
        data = b"".join(self._buf)
        self._buf = []
        return self.turn_seq, data
