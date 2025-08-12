# voice_routes.py — blueprint for voice, with "email me ..." intents
import re
import json
import requests
from flask import Blueprint, request, jsonify, session

# deps expected from app.py:
#   - oai
#   - generate_chip_response (callable)
#   - eleven, voice_id, TTS_ENABLED  (not required here unless you want to synthesize server-side audio)

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
        Request JSON (examples):
          { "transcript": "email me the account team for Acme" }
          { "transcript": "email me the FlashBlade presentation" }
          { "transcript": "email me that" }
          { "transcript": "who owns Acme account?" }
        Response JSON:
          { "reply_text": "...", "reply": "..." }
        """
        data = request.get_json(force=True) or {}
        transcript = (data.get("transcript") or data.get("text") or "").strip()
        if not transcript:
            return jsonify({"reply_text": "I didn’t catch that.", "reply": "I didn’t catch that."})

        # Session context for personalization + email target
        user_id = (session.get("user_id") or "").strip().lower()
        name = session.get("name") or (user_id or "there")
        role = session.get("role") or "engineer"
        region = session.get("region") or "NA"

        # --------- Fast-path: handle "email me ..." BEFORE LLM ----------
        intent = parse_email_intent(transcript)
        if intent:
            if not user_id:
                reply = "Please log in so I know where to send it."
                _safe_log(user_id, transcript, reply)
                return jsonify({"reply_text": reply, "reply": reply})

            base = request.host_url.rstrip("/")
            try:
                if intent["type"] == "acct_team":
                    company = intent["company"]
                    r = requests.post(
                        f"{base}/email/account-team",
                        json={"company": company},
                        timeout=20,
                    )
                    if r.ok:
                        reply = f"I’ve emailed you the team for {company}."
                    else:
                        reply = _friendly_error(r) or f"I couldn’t send the email for {company}."
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "doc_link":
                    title_hint = intent["title"]

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
                        _safe_log(user_id, transcript, reply)
                        return jsonify({"reply_text": reply, "reply": reply})

                    r = requests.post(
                        f"{base}/email/repo-link",
                        json={"doc_id": doc["id"]},
                        timeout=20,
                    )
                    if r.ok:
                        reply = "Sent. Check your inbox."
                    else:
                        reply = _friendly_error(r) or "I couldn’t send that email."
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

                if intent["type"] == "email_last":
                    r = requests.post(f"{base}/email/last", timeout=20)
                    if r.ok:
                        reply = "Emailed."
                    else:
                        reply = _friendly_error(r) or "I don’t have anything to email yet."
                    _safe_log(user_id, transcript, reply)
                    return jsonify({"reply_text": reply, "reply": reply})

            except requests.RequestException:
                reply = "Email looks down—try again in a bit."
                _safe_log(user_id, transcript, reply)
                return jsonify({"reply_text": reply, "reply": reply})

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
