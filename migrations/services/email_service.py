import os, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(to_addr: str, subject: str, body: str, html: str=None):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    from_addr = os.getenv("SMTP_FROM") or user
    if not (host and user and pwd and from_addr and to_addr):
        return False, "SMTP not fully configured"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or ""
    msg["From"] = from_addr
    msg["To"] = to_addr
    if body:
        msg.attach(MIMEText(body, "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.sendmail(from_addr, [to_addr], msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)
