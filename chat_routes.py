# chat_routes.py — text chat endpoints and chat helpers
import os
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request

# DB logging & user context
from memory import log_conversation, get_user

# OpenAI SDK (chat-completions path)
from openai import OpenAI
_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # snappy default
WORD_LIMIT = int(os.getenv("CHAT_WORD_LIMIT", "30"))  # guardrail per user preference

chat_bp = Blueprint("chat", __name__)


def _limit_words(text: str, max_words: int = WORD_LIMIT) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return text or ""
    return " ".join(words[:max_words]).rstrip(",.;:!?")


def _system_prompt():
    # Personality + rails (Pure-focused, concise, human, Nebraskan vibe)
    return (
        "You are Chip Tracewell, a humble, sharp, Nebraskan virtual systems engineer for Pure Storage. "
        "Speak plainly, be personable, helpful, and keep answers under 30 words unless asked otherwise. "
        "Stay focused on Pure Storage products, architectures, and closely related tech."
    )


@chat_bp.post("/api/chat")
def api_chat():
    """
    Synchronous text chat endpoint.
    Request JSON: { message, email?, meta? }
    Response JSON: { reply, tokens?, finish_reason? }
    """
    data = request.get_json(force=True, silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "message required"}), 400

    email = (data.get("email") or request.headers.get("X-User-Email") or "").strip() or None
    meta = data.get("meta") or {}

    # Personalize tone w/ user context (best effort)
    name = title = ""
    if email:
        user = get_user(email) or {}
        name = user.get("name") or ""
        title = user.get("title") or ""

    system_msg = _system_prompt()
    if name or title:
        system_msg += f" The user is {name}{f' ({title})' if title else ''}."

    try:
        completion = _openai.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_message},
            ],
            temperature=0.4,
            max_tokens=180,
        )
        reply = (completion.choices[0].message.content or "").strip()
        reply = _limit_words(reply, WORD_LIMIT)

        # DB logs (best effort)
        now = datetime.utcnow().isoformat()
        if email:
            log_conversation(email, "user", user_message, meta={"t": now, **meta})
            log_conversation(email, "assistant", reply, meta={"t": now})

        usage = completion.usage
        return jsonify(
            {
                "reply": reply,
                "finish_reason": completion.choices[0].finish_reason,
                "tokens": {
                    "prompt": getattr(usage, "prompt_tokens", None),
                    "completion": getattr(usage, "completion_tokens", None),
                    "total": getattr(usage, "total_tokens", None),
                },
            }
        )
    except Exception as e:
        current_app.logger.exception("api_chat failed")
        return jsonify({"error": "chat_failed", "detail": str(e)}), 500


@chat_bp.post("/api/chat/suggestions")
def api_chat_suggestions():
    """
    Lightweight suggestion generator for UI quick-reply chips.
    Request: { topic?, email? }
    Response: { suggestions: [...] }
    """
    data = request.get_json(force=True, silent=True) or {}
    topic = (data.get("topic") or "Pure Storage").strip()
    email = (data.get("email") or "").strip() or None

    user = get_user(email) if email else None
    default = [
        "Show me how to install a FlashArray",
        "Summarize the latest Portworx features",
        "Explain NVMe/TCP like I’m new to storage",
        "What’s new in FlashBlade//S?",
    ]

    if user and (user.get("title") or "").lower().find("sales") >= 0:
        default = [
            "Business value of FlashBlade//E",
            "Talk tracks: NVMe/TCP vs iSCSI",
            "Where does Portworx fit in Kubernetes?",
            "Top benefits of Evergreen//One",
        ]

    # Topic hint can slightly bias suggestions
    if "k8s" in topic.lower() or "kubernetes" in topic.lower():
        default = [
            "Portworx Data Services in 30 words",
            "PX-Backup vs Velero — when to use each?",
            "Design a 3-node k8s storage layout",
            "Explain StorageClasses for Portworx",
        ]

    return jsonify({"suggestions": default})
