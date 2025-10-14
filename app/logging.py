from .db import db
from .admin_events import admin_events


def _emit_admin_log(message: str, email: str, role: str) -> None:
    try:
        from .admin_log import emit as admin_emit  # type: ignore
    except Exception:
        admin_emit = None

    if callable(admin_emit):
        try:
            admin_emit("admin_log", email=email, role=role, message=message)
        except Exception:
            pass


def admin_log(message: str, email: str = "system", role: str = "system"):
    payload = {"email": email, "role": role, "message": message}
    db.memory['logs'].append(payload)
    admin_events.emit("audit", payload)
    _emit_admin_log(message=message, email=email, role=role)
