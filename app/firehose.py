from __future__ import annotations
import os


def is_firehose_enabled() -> bool:
    """
    Firehose mode: return True when we want all logs at DEBUG with no
    category clamping. Controlled by env FIREHOSE_LOGS.
    """
    val = os.getenv("FIREHOSE_LOGS", "")
    return val.lower() in ("1", "true", "yes", "on")
