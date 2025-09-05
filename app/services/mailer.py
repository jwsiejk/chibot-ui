# app/services/mailer.py
import os, smtplib, time, hashlib
from email.mime.text import MIMEText

# Module-level idempotency map (reset on process restart)
_EMAIL_SENT: dict[str, float] = {}

def _smtp_ready():
    required = ["EMAIL_HOST","EMAIL_PORT","EMAIL_HOST_USER","EMAIL_HOST_PASSWORD","FROM_EMAIL"]
    return all(os.environ.get(k) for k in required)

def _email_key(email: str, subject: str, body: str) -> str:
    src = f"{email}|{subject}|{body}".encode("utf-8")
    return hashlib.sha256(src).hexdigest()

def send_transcript(email: str, subject: str, body: str) -> bool:
    if not _smtp_ready():
        raise RuntimeError("SMTP not configured; set EMAIL_* and FROM_EMAIL")
    ttl = int(os.environ.get("EMAIL_DEDUPE_TTL","900"))
    kid = _email_key(email or "", subject or "", body or "")
    tnow = time.time()
    tprev = _EMAIL_SENT.get(kid, 0)
    if tprev and (tnow - tprev) < ttl:
        return True

    msg = MIMEText(body or "")
    msg["Subject"] = subject or "Transcript"
    msg["From"] = f"{os.environ.get('EMAIL_FROM_NAME','Chip')} <{os.environ.get('FROM_EMAIL')}>"
    msg["To"] = email

    host, port = os.environ["EMAIL_HOST"], int(os.environ["EMAIL_PORT"])
    retries = int(os.environ.get("EMAIL_RETRIES","2"))
    use_tls = os.environ.get("EMAIL_USE_TLS","true").lower() in ("1","true","yes")

    last_err = None
    for attempt in range(retries+1):
        try:
            server = smtplib.SMTP(host, port, timeout=int(os.environ.get("EMAIL_TIMEOUT","30")))
            if use_tls:
                server.starttls()
            server.login(os.environ["EMAIL_HOST_USER"], os.environ["EMAIL_HOST_PASSWORD"])
            server.sendmail(os.environ["FROM_EMAIL"], [email], msg.as_string())
            try:
                server.quit()
            except Exception:
                pass
            _EMAIL_SENT[kid] = tnow
            return True
        except Exception as e:
            last_err = e
            time.sleep(min(2.0, 0.2 * (2 ** attempt)))
    raise last_err or RuntimeError("email send failed")
