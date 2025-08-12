import re
from flask import request, jsonify, session

EMAIL_INTENTS = {
    "acct_team": re.compile(
        r"\bemail me (?:the )?account team (?:for|at)\s+(?P<company>.+?)[\.\?!]*$",
        re.IGNORECASE
    ),
    "doc_link": re.compile(
        r"\bemail me (?:that |the )?(?P<title>.+?)\s*(?:presentation|deck|slides)\b",
        re.IGNORECASE
    ),
    "just_email_this": re.compile(
        r"\bemail (?:me|this to me|that to me)\b", re.IGNORECASE
    ),
}

def parse_email_intent(text: str):
    t = (text or "").strip()
    if not t:
        return None
    m = EMAIL_INTENTS["acct_team"].search(t)
    if m:
        return {"type": "acct_team", "company": m.group("company").strip()}
    m = EMAIL_INTENTS["doc_link"].search(t)
    if m:
        return {"type": "doc_link", "title": m.group("title").strip()}
    m = EMAIL_INTENTS["just_email_this"].search(t)
    if m:
        return {"type": "email_last"}
    return None
