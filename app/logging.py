from .db import db
from .admin_events import admin_events
def admin_log(message: str, email: str = "system", role: str = "system"):
    db.memory['logs'].append({'email': email, 'role': role, 'message': message})
    admin_events.emit("audit", {"email": email, "role": role, "message": message})
