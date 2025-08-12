# voice_routes.py — blueprint for voice (with "email me ..." + account intents)
import re
import json
import requests
from flask import Blueprint, request, jsonify, session

# ----------------------------
# Intent patterns for "email me"
# ----------------------------
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


# ----------------------------
# Account team / contact intents
# ----------------------------
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

def handle_account_intents(user_text: str, base_url: str) -> str | None:
    m = ACCOUNT_INTENTS["team_for"].search(user_text) or ACCOUNT_INTENTS["contact_for"].search(user_text)
    if m:
        company = m.group("company").strip(" .?!")
        r = requests.get(f"{base_url}/accounts/search", params={"q": company}, timeout=15)
        js = r.json() if r.ok else {}
        row = (js.get("result") or None)
        if not row:
            return f"I couldn’t find “{company}”. Want me to try a broader match?"

        session["last_account_name"] = row.get("account_name")

        if ACCOUNT_INTENTS["team_for"].search(user_text):
            team = compose_team_line(row)
            opener = "Let me get that for you. " if team else "Here’s what I have. "
            tail = " Want me to email this to you?" if team else ""
            return (opener + (team or "I don’t have team details on file.") + tail).strip()

        if ACCOUNT_INTENTS["contact_for"].search(user_text):
            email = primary_contact_email(row)
            if email:
                return f"I have the Pure AE’s email for {row['account_name']}: {email}. Want the rest by email?"
            return f"I don’t have contacts on file for {row['account_name']}. Want me to email the owner to introduce you?"

    if ACCOUNT_INTENTS["contact_followup"].search(user_text):
        last_acct = session.get("last_account_name")
        if not last_acct:
            return "For which account? If you say the name, I’ll pull the contacts."
        r = requests.get(f"{base_url}/accounts/search", params={"q": last_acct}, timeout=15)
        js = r.json() if r.ok else {}
        row = (js.get("result") or None)
        if not row:
            return "I lost the last account. Say the name again and I’ll grab it."
        email = primary_contact_email(row)
        if email:
            return f"The Pure AE’s email is {email}. Want me to email the full team list to you?"
        return "I don’t have contacts on file. Want me to send an intro email to the owner?"
    return None


def create_voice_blueprint(deps: dict):
    """
    deps:
      - oai
      - generate_chip_response (callable)
      - eleven, voice_id, TTS_ENABLED (optional here)
    """
    bp = Blueprint("voice", __name__)
    generate_chip_response = deps["generate_chip_response"]

    @bp.post("/voice")
    def voice():
        """
        Accepts recognized speech as text and returns Chip's reply text.
        Request JSON:
          { "transcript": "..." }  or  { "text": "..." }
        Response JSON:
          { "reply_text": "...", "reply": "..." }
        """
        data = request.get_json(force=True) or {}
        transcript = (data.get("transcript") or data.get("text") or "").strip()
        if not transcript:
            return jsonify({"reply_text": "I didn’t catch that.", "reply": "I didn’t catch that."})

        user_id = (session.get("user_id") or "").strip().lower()
        name = session.get("name") or (user_id or "there")
        role = session.get("role") or "engineer"
        region = session.get("region") or "NA"

        base = request.host_url.rstrip("/")

        # --------- Fast-path A: "email me ..." intents ----------
        intent = parse_email_intent(transcript)
        if intent:
            if not user_id:
                reply = "Please log in so I know where to send it."
                _safe_log(user_id, transcript, reply)
                return jsonify({"reply_text": reply, "reply": reply})
            try:
                if intent["type"] == "acct_team":
                    company = intent["company"]
                    r = requests.post(f"{base}/email/account-team", json={"company": company}, timeout=20)
                    reply = f"I’ve emailed you the team for {company}." if r.ok else (_friendly_error(r) or f"I couldn’t send the email for {company}.")
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "doc_link":
                    title_hint = intent["title"]
                    sr = requests.get(f"{base}/repo/search", params={"q": title_hint}, timeout=20)
                    doc = None
                    if sr.ok:
                        js = sr.json() or {}
                        results = js.get("results") or []
                        if results:
                            doc = results[0]
                    if not doc:
                        reply = f"I couldn’t find a deck matching “{title_hint}”."
                        _safe_log(user_id, transcript, reply)
                        return jsonify({"reply_text": reply, "reply": reply})
                    r = requests.post(f"{base}/email/repo-link", json={"doc_id": doc["id"]}, timeout=20)
                    reply = "Sent. Check your inbox." if r.ok else (_friendly_error(r) or "I couldn’t send that email.")
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "email_last":
                    r = requests.post(f"{base}/email/last", timeout=20)
                    reply = "Emailed." if r.ok else (_friendly_error(r) or "I don’t have anything to email yet.")
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

            except requests.RequestException:
                reply = "Email looks down—try again in a bit."
                _safe_log(user_id, transcript, reply)
                return jsonify({"reply_text": reply, "reply": reply})

        # --------- Fast-path B: account team / contact intents ----------
        quick = handle_account_intents(transcript, base)
        if quick:
            _safe_log(user_id, transcript, quick)
            return jsonify({"reply_text": quick, "reply": quick})

        # --------- Normal path: LLM reply ----------
        reply_text = generate_chip_response(user_id, name, transcript, role, region)
        _safe_log(user_id, transcript, reply_text)
        return jsonify({"reply_text": reply_text, "reply": reply_text})

    return bp


def _friendly_error(resp: requests.Response) -> str | None:
    """Turn a failed endpoint call into a friendly message."""
    try:
        js = resp.json()
        if isinstance(js, dict) and "error" in js:
            err = (js.get("error") or "").strip().lower()
            if "unauthenticated" in err:
                return "Please log in first."
            if "not found" in err:
                return "I couldn’t find that."
            if "missing" in err:
                return "I’m missing a detail to send that."
            return js.get("error")
    except Exception:
        pass
    return None


def _safe_log(user_id: str, user_text: str, reply_text: str):
    """Best‑effort conversation logging."""
    try:
        from memory import log_conversation  # import here to avoid hard dependency at module import
        if user_id:
            log_conversation(user_id, user_text, reply_text)
    except Exception:
        pass
