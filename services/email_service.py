# services/email_service.py
# Render-safe Email sender for Ask Chip using a Google Apps Script Web App webhook.
# Keeps the public API identical:
#   send_email(to, subject, html=None, text=None, reply_to=None) -> bool
#
# Configure in Render:
#   EMAIL_WEBHOOK_URL   = https://script.google.com/macros/s/AKfycb.../exec
#   EMAIL_WEBHOOK_SECRET= <your long random secret>
#   EMAIL_FROM_NAME     = Ask Chip   (optional)
#
# This replaces the SMTP implementation (which does not work on Render).

from __future__ import annotations
import os, json, urllib.request

WEBHOOK_URL   = (os.environ.get("EMAIL_WEBHOOK_URL") or "").strip()
WEBHOOK_SECRET= (os.environ.get("EMAIL_WEBHOOK_SECRET") or "").strip()
FROM_NAME     = (os.environ.get("EMAIL_FROM_NAME") or "Ask Chip").strip()

def _post_json(url: str, payload: dict, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read().decode("utf-8")

def send_email(to, subject, html=None, text=None, reply_to=None) -> bool:
    """
    Send an email via Apps Script webhook.
    Returns True on success, False on failure.
    """
    # normalize recipients
    if isinstance(to, str):
        to = [to]
    to = [addr for addr in (to or []) if addr]
    if not to or not subject:
        return False

    if not WEBHOOK_URL or not WEBHOOK_SECRET:
        # Not configured
        return False

    base = {
        "secret": WEBHOOK_SECRET,
        "from_name": FROM_NAME,
        "subject": subject,
        "html": html or "",
        "text": text or "",
        "reply_to": reply_to or "",
    }

    # Send one-by-one to keep the webhook simple and to respect quotas
    for addr in to:
        payload = dict(base, to=addr)
        try:
            status, body = _post_json(WEBHOOK_URL, payload)
            if status != 200 or '"ok": true' not in body:
                return False
        except Exception:
            return False
    return True