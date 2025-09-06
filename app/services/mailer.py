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
