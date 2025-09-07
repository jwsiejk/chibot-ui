import os
def get_admin_emails():
    raw = os.environ.get("ADMIN_EMAILS", "")
    return set([e.strip().lower() for e in raw.split(",") if e.strip()])
def is_admin_email(email: str) -> bool:
    if not email: return False
    return email.strip().lower() in get_admin_emails()
