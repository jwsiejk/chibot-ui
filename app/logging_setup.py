import logging
import time
from contextvars import ContextVar
from typing import Optional

current_sid: ContextVar[Optional[str]] = ContextVar("current_sid", default=None)


class TelemetryBusHandler(logging.Handler):
    def __init__(self, bus, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - defensive
        try:
            payload = {
                "sid": current_sid.get() or "unspecified",
                "ts": time.time(),
                "logger": record.name,
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            for key in ("req_id", "turn_id", "event", "sub"):
                if hasattr(record, key):
                    payload[key] = getattr(record, key)
            event = {"type": "EVT_LOG", **payload}
            self.bus.publish(event)
        except Exception:
            # Never allow telemetry logging to break the main logging flow.
            pass


def install_bus_handler(bus, level: int = logging.INFO) -> None:
    root = logging.getLogger()

    # Ensure there is always at least one non-telemetry handler so log events
    # continue to reach the standard console/server outputs.  When the runtime
    # invokes the bus installer before any other logging configuration this
    # guarantees that `logging.basicConfig` wires up the default stream
    # handler.  If the only existing handlers are telemetry handlers we add a
    # lightweight stream handler explicitly to preserve console visibility.
    if not any(not isinstance(handler, TelemetryBusHandler) for handler in root.handlers):
        if not root.handlers:
            logging.basicConfig(level=level)
            root = logging.getLogger()
        else:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(level)
            root.addHandler(stream_handler)

    if any(isinstance(handler, TelemetryBusHandler) for handler in root.handlers):
        return
    handler = TelemetryBusHandler(bus, level)
    handler.setLevel(level)
    root.addHandler(handler)
