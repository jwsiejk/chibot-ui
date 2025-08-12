# chat_routes.py — blueprint for chat (with "email me ..." intents)
import re
import json
import requests
from flask import Blueprint, request, jsonify, session

# Optional: write chat history to DB so the email actions are in the log, too.
try:
    from memory import log_conversation
except Exception:
    def log_conversation(*args, **kwargs):
        return None  # no-op fallback


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


def create_chat_blueprint(deps: dict):
    """
    deps:
      - oai
      - generate_chip_response (callable)
    """
    bp = Blueprint("chat", __name__)
    generate_chip_response = deps["generate_chip_response"]

    @bp.post("/chat")
    def chat():
        """
        Request JSON:
          { "message": "text", "lane": "...", "language": "en", "domain": "pure-storage" }
        Response JSON:
          { "reply_text": "...", "reply": "..." }
        """
        data = request.get_json(force=True) or {}
        user_text = (data.get("message") or "").strip()
        if not user_text:
            return jsonify({"reply_text": "Say that again?", "reply": "Say that again?"})

        # Ensure user is known (for email actions)
        user_id = (session.get("user_id") or "").strip().lower()
        name = session.get("name") or (user_id or "there")
        role = session.get("role") or "engineer"
        region = session.get("region") or "NA"

        # --------- Fast-path: handle "email me ..." intents BEFORE LLM ----------
        intent = parse_email_intent(user_text)
        if intent:
            if not user_id:
                reply = "You’ll need to be logged in so I know where to send it."
                _safe_log(user_id, user_text, reply)
                return jsonify({"reply_text": reply, "reply": reply})

            base = request.host_url.rstrip("/")

            try:
                if intent["type"] == "acct_team":
                    company = intent["company"]
                    # We could normalize the company via /accounts/search if desired, but it's optional.
                    r = requests.post(
                        f"{base}/email/account-team",
                        json={"company": company},
                        timeout=20,
                    )
                    if r.ok:
                        reply = f"I’ve emailed you the team for {company}."
                    else:
                        # Surface a friendly message without leaking internals
                        msg = _friendly_error(r)
                        reply = msg or f"I couldn’t send the email for {company}."
                    _safe_log(user_id, user_text, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "doc_link":
                    title_hint = intent["title"]

                    # Find the best matching document via your repo search API
                    sr = requests.get(
                        f"{base}/repo/search",
                        params={"q": title_hint},
                        timeout=20,
                    )
                    doc = None
                    if sr.ok:
                        js = sr.json() or {}
                        results = js.get("results") or []
                        if results:
                            doc = results[0]

                    if not doc:
                        reply = f"I couldn’t find a deck matching “{title_hint}”."
                        _safe_log(user_id, user_text, reply)
                        return jsonify({"reply_text": reply, "reply": reply})

                    # Email the link using your new endpoint
                    r = requests.post(
                        f"{base}/email/repo-link",
                        json={"doc_id": doc["id"]},
                        timeout=20,
                    )
                    if r.ok:
                        reply = "Sent. Check your inbox."
                    else:
                        msg = _friendly_error(r)
                        reply = msg or "I couldn’t send that email."
                    _safe_log(user_id, user_text, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "email_last":
                    r = requests.post(f"{base}/email/last", timeout=20)
                    if r.ok:
                        reply = "Emailed."
                    else:
                        msg = _friendly_error(r)
                        reply = msg or "I don’t have anything to email yet."
                    _safe_log(user_id, user_text, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

            except requests.RequestException:
                reply = "Email looks down—try again in a bit."
                _safe_log(user_id, user_text, reply)
                return jsonify({"reply_text": reply, "reply": reply})

        # --------- Normal path: generate Chip’s response via LLM ----------
        reply_text = generate_chip_response(user_id, name, user_text, role, region)
        _safe_log(user_id, user_text, reply_text)
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
    """Best-effort conversation logging."""
    try:
        if not user_id:
            return
        log_conversation(user_id, user_text, reply_text)
    except Exception:
        pass
