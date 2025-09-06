import os, smtplib
from email.mime.text import MIMEText
from ..db import db

def _smtp_ready():
    return all(os.environ.get(k) for k in ["EMAIL_HOST","EMAIL_PORT","EMAIL_HOST_USER","EMAIL_HOST_PASSWORD","FROM_EMAIL"])

def send_transcript(email: str, subject: str, body: str) -> bool:
    # In tests or when SMTP is not configured, record to DB (mock)
    prod = (os.environ.get("APP_ENV","").lower() in ("prod","production") or os.environ.get("ENV","").lower() in ("prod","production"))
    allow_mock = os.environ.get("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
    if os.environ.get("USE_MOCK_VENDORS") == "1" or not _smtp_ready():
        if prod and not allow_mock:
            raise RuntimeError("SMTP not configured and mocks disallowed in production")
        db.add_email(email, subject, body)
        return True

    msg = MIMEText(body or "")
    msg["Subject"] = subject or "Transcript"
    msg["From"] = os.environ.get("FROM_EMAIL")
    msg["To"] = email
    host = os.environ.get("EMAIL_HOST"); port = int(os.environ.get("EMAIL_PORT","587"))
    user = os.environ.get("EMAIL_HOST_USER"); pwd = os.environ.get("EMAIL_HOST_PASSWORD")
    use_tls = os.environ.get("EMAIL_USE_TLS","true").lower() in ("1","true","yes")
    s = smtplib.SMTP(host, port, timeout=15)
    try:
        if use_tls: s.starttls()
        if user: s.login(user, pwd)
        s.send_message(msg)
        db.add_email(email, subject, body)  # also record for history
        return True
    finally:
        try: s.quit()
        except Exception: pass