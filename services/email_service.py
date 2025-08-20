# services/email_service.py
import os, smtplib, ssl, sys
from email.message import EmailMessage

def _env_bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _smtp():
    host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    port = int(os.getenv("EMAIL_PORT", "587"))
    user = os.getenv("EMAIL_HOST_USER")  # e.g. your full gmail address
    pwd  = os.getenv("EMAIL_HOST_PASSWORD")  # Gmail App Password recommended
    use_tls = _env_bool(os.getenv("EMAIL_USE_TLS", "true"), True)
    use_ssl = _env_bool(os.getenv("EMAIL_USE_SSL", "false"), False)

    if not (host and port and user and pwd):
        raise RuntimeError("SMTP not configured (missing host/port/user/password)")

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        if use_tls:
            server.starttls(context=ssl.create_default_context())

    server.login(user, pwd)
    return server

def send_email(to, subject, html=None, text=None, reply_to=None):
    """
    Send an email. Returns True on success, False on failure.
    `to` can be a string or a list of addresses.
    """
    if isinstance(to, str):
        to = [to]

    from_addr = os.getenv("FROM_EMAIL") or os.getenv("EMAIL_HOST_USER")
    if not from_addr:
        sys.stderr.write("[warning] send_email: FROM_EMAIL/EMAIL_HOST_USER not set\n")
        return False
    if not html and not text:
        text = ""  # ensure there is at least a plain part

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if reply_to:
        msg["Reply-To"] = reply_to

    if html and text:
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    elif html:
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(text or "")

    try:
        with _smtp() as s:
            s.send_message(msg)
        return True
    except Exception as e:
        sys.stderr.write(f"[warning] send_email failed: {e}\n")
        return False
