from __future__ import annotations
import os, json, time, uuid
from typing import Dict, Any, Optional

# Mailer routes via Outbox rather than sending synchronously
from app.services.outbox import enqueue_item

FROM_EMAIL = os.environ.get("FROM_EMAIL", "chip@example.com")

def queue_transcript_email(session_id: str, ended_at: str, to_email: str, subject: str, body: str) -> str:
    payload = {
        "to": to_email,
        "subject": subject,
        "body": body,
        "from": FROM_EMAIL
    }
    return enqueue_item(kind="transcript_email", dedupe_key=(session_id, ended_at), payload=payload, session_id=session_id, ended_at=ended_at)


def send_transcript(session_id: str, ended_at: str, to_email: str, subject: str, body: str) -> bool:
    """
    Backward-compatible shim: immediately enqueue transcript email.
    Returns True to indicate it's been queued; delivery handled by outbox worker.
    """
    queue_transcript_email(session_id=session_id, ended_at=ended_at, to_email=to_email, subject=subject, body=body)
    return True


def send_transcript(*args, **kwargs) -> bool:
    """
    Backward-compatible shim supporting:
      - send_transcript(to_email, subject, body)
      - send_transcript(session_id, ended_at, to_email, subject, body)
      - or keyword equivalents.
    Always enqueues to Outbox and returns True.
    """
    to_email = kwargs.pop("to_email", None)
    subject = kwargs.pop("subject", None)
    body = kwargs.pop("body", None)
    session_id = kwargs.pop("session_id", None)
    ended_at = kwargs.pop("ended_at", None)

    if len(args) == 3 and not to_email:
        to_email, subject, body = args
    elif len(args) >= 5 and not (session_id and ended_at and to_email and subject and body):
        session_id, ended_at, to_email, subject, body = args[:5]

    # Defaults for legacy form
    from datetime import datetime, timezone
    if not session_id: session_id = "adhoc"
    if not ended_at: ended_at = datetime.now(timezone.utc).isoformat()

    if not (to_email and subject is not None and body is not None):
        return False

    queue_transcript_email(session_id=session_id, ended_at=ended_at, to_email=to_email, subject=subject, body=body)
    return True
