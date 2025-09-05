# app/services/mailer.py
import os, smtplib
from email.mime.text import MIMEText
from ..db import db

def _smtp_ready():
    required = ["EMAIL_HOST","EMAIL_PORT","EMAIL_HOST_USER","EMAIL_HOST_PASSWORD","FROM_EMAIL"]
    return all(os.environ.get(k) for k in required)

def send_transcript(email: str, subject: str, body: str) -> bool:
    if not _smtp_ready():
        raise RuntimeError("SMTP not configured; set EMAIL_* and FROM_EMAIL")
    msg = MIMEText(body or "")
    msg["Subject"] = subject or "Transcript"
    msg["From"] = f"{os.environ.get('EMAIL_FROM_NAME','Chip')} <{os.environ.get('FROM_EMAIL')}>"
    msg["To"] = email
    server = smtplib.SMTP(os.environ["EMAIL_HOST"], int(os.environ["EMAIL_PORT"]))
    try:
        if os.environ.get("EMAIL_USE_TLS","true").lower() in ("1","true","yes"):
            server.starttls()
        server.login(os.environ["EMAIL_HOST_USER"], os.environ["EMAIL_HOST_PASSWORD"])
        server.sendmail(os.environ["FROM_EMAIL"], [email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
    # Log to DB for admin visibility
    db.add_email(email, subject or "Transcript", body or "")
    return True
