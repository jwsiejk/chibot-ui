# services/logs.py
try:
    from memory import log_conversation as _log
except Exception:
    _log = None

def log_conversation(user_id: str, user_text: str, reply_text: str, meta=None) -> None:
    if _log:
        try:
            _log(user_id, user_text, reply_text if reply_text is not None else "", meta or {})
        except Exception:
            pass
