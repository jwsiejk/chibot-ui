# services/intents.py
import re
from flask import session
import requests

EMAIL_INTENTS = {
    "acct_team": re.compile(
        r"\bemail\s+me\s+(?:the\s+)?account\s+team\s+(?:for|at)\s+(?P<company>.+?)[\.\?!]*$",
        re.IGNORECASE,
    ),
    "doc_link": re.compile(
        r"\bemail\s+me\s+(?:that\s+|the\s+)?(?P<title>.+?)\s*(?:presentation|deck|slides)\b",
        re.IGNORECASE,
    ),
    "email_last": re.compile(
        r"\bemail\s+(?:me|this\s+to\s+me|that\s+to\s+me|it\s+to\s+me)\b",
        re.IGNORECASE,
    ),
}

ACCOUNT_INTENTS = {
    "team_for": re.compile(
        r"\b(?:who\s+is\s+)?(?:the\s+)?account\s+team\s+(?:for|at)\s+(?P<company>.+?)\s*\?*$",
        re.IGNORECASE),
    "contact_for": re.compile(
        r"\b(?:do\s+you\s+have\s+)?(?:the\s+)?contact\s+(?:info|information)\s+(?:for|for\s+the|at)\s+(?P<company>.+?)\s*\?*$",
        re.IGNORECASE),
    "contact_followup": re.compile(
        r"\b(?:do\s+you\s+have\s+their|what(?:'s| is)\s+their)\s+contact\s+(?:info|information|email)\b",
        re.IGNORECASE),
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
    m = EMAIL_INTENTS["email_last"].search(t)
    if m:
        return {"type": "email_last"}
    return None

def _human_join(parts):
    parts = [p for p in parts if p]
    if not parts: return ""
    if len(parts) == 1: return parts[0]
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"

def _role_piece(label, name, email=None):
    if not name and not email:
        return None
    if name and email:
        return f"{label} is {name} ({email})"
    if name:
        return f"{label} is {name}"
    if email:
        return f"{label}: {email}"
    return None

def compose_team_line(row: dict) -> str:
    parts = []
    parts.append(_role_piece("Pure AE", row.get("owner"), row.get("owner_email")))
    parts.append(_role_piece("Manager", row.get("manager")))
    parts.append(_role_piece("PAM", row.get("pam")))
    parts.append(_role_piece("RSD", row.get("rsd"), row.get("rsd_email")))  # optional
    txt = _human_join(parts)
    if row.get("region"):
        txt = (txt + f". Region: {row['region']}").strip()
    return txt

def primary_contact_email(row: dict) -> str | None:
    return row.get("owner_email") or row.get("rsd_email")

def handle_account_intents(text: str, base_url: str, sess) -> str | None:
    m = ACCOUNT_INTENTS["team_for"].search(text) or ACCOUNT_INTENTS["contact_for"].search(text)
    if m:
        company = m.group("company").strip(" .?!")
        try:
            r = requests.get(f"{base_url}/accounts/search", params={"q": company}, timeout=15)
        except requests.RequestException:
            return "Account search looks down—try again soon."
        js = r.json() if r.ok else {}
        row = js.get("result") or None
        if not row:
            return f"I couldn’t find “{company}”. Want me to try a broader match?"
        sess["last_account_name"] = row.get("account_name")
        if ACCOUNT_INTENTS["team_for"].search(text):
            team = compose_team_line(row)
            opener = "Let me get that for you. " if team else "Here’s what I have. "
            tail = " Want me to email this to you?" if team else ""
            return (opener + (team or "I don’t have team details on file.") + tail).strip()
        if ACCOUNT_INTENTS["contact_for"].search(text):
            email = primary_contact_email(row)
            if email:
                return f"I have the Pure AE’s email for {row['account_name']}: {email}. Want the rest by email?"
            return f"I don’t have contacts on file for {row['account_name']}. Want me to email the owner to introduce you?"
    if ACCOUNT_INTENTS["contact_followup"].search(text):
        last_acct = session.get("last_account_name")
        if not last_acct:
            return "For which account? If you say the name, I’ll pull the contacts."
        try:
            r = requests.get(f"{base_url}/accounts/search", params={"q": last_acct}, timeout=15)
        except requests.RequestException:
            return "Account search looks down—try again soon."
        js = r.json() if r.ok else {}
        row = js.get("result") or None
        if not row:
            return "I lost the last account. Say the name again and I’ll grab it."
        email = primary_contact_email(row)
        if email:
            return f"The Pure AE’s email is {email}. Want me to email the full team list to you?"
        return "I don’t have contacts on file. Want me to send an intro email to the owner?"
    return None
