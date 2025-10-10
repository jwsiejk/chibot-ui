from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Detection:
    container: str
    codec: str
    containerized: bool = True

class AudioContainerSniffer:
    EBML_MAGIC = bytes([0x1A, 0x45, 0xDF, 0xA3])
    OGG_MAGIC = b'OggS'
    MAX_WINDOW = 64

    def __init__(self) -> None:
        self._buf = bytearray()
        self._detected: Optional[Detection] = None
        # optional MIME hint
        self._mime: Optional[str] = None

    @property
    def detected(self) -> Optional[Detection]:
        return self._detected

    def feed(self, chunk: bytes) -> Optional[Detection]:
        if self._detected or not chunk:
            return self._detected
        self._buf += chunk
        if len(self._buf) > self.MAX_WINDOW:
            del self._buf[:-self.MAX_WINDOW]
        if self.EBML_MAGIC in self._buf:
            self._detected = Detection(container="webm", codec="opus", containerized=True)
        elif self.OGG_MAGIC in self._buf:
            self._detected = Detection(container="ogg", codec="opus", containerized=True)
        return self._detected

    # Optional MIME hint storage (e.g., from client headers); used by ws layer for logging.
    def set_meta(self, mime: Optional[str]) -> None:
        try:
            self._mime = (mime or "").strip() or None
        except Exception:
            self._mime = None

    def meta(self) -> Optional[str]:
        # Return last seen MIME hint, if any. Container detection is based on bytes in feed().
        return getattr(self, "_mime", None)

def coerce_detection_from_meta(mime: Optional[str]) -> Optional[Detection]:
    if not mime:
        return None
    m = mime.lower()
    if "opus" in m and "webm" in m:
        return Detection(container="webm", codec="opus", containerized=True)
    if "opus" in m and "ogg" in m:
        return Detection(container="ogg", codec="opus", containerized=True)
    return None
