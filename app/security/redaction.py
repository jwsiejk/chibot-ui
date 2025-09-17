
import re

# Very simple placeholder — avoids leaking obvious emails/phones in logs.
# Expandable via admin-configured patterns.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){7,}\d")

def redact_pii(text: str) -> str:
    if not text:
        return text
    text = EMAIL_RE.sub("[redacted-email]", text)
    text = PHONE_RE.sub("[redacted-phone]", text)
    return text
