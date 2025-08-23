from flask import Blueprint, request, jsonify
from services.llm_service import generate_response

# --- Blueprint must be defined BEFORE any @conversation_bp.route decorators ---
conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")


# ----------------------------- Existing endpoint -----------------------------
# Kept for backward compatibility; behavior preserved.
@conversation_bp.route("/chat_orchestrated", methods=["POST", "OPTIONS"])
def chat_orchestrated():
    if request.method == "OPTIONS":
        # Let CORS middleware finish the preflight
        return ("", 204)

    data = request.get_json(force=True, silent=True) or {}
    user_text = (data.get("message") or data.get("text") or data.get("prompt") or "").strip()
    history = data.get("history") or data.get("messages") or []

    if not user_text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    try:
        resp = generate_response(user_text=user_text, history=history)
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

    # Normalize output to a stable shape without breaking callers that expect {"text": ...}
    if isinstance(resp, dict) and "text" in resp:
        return jsonify({"ok": True, **resp}), 200
    if isinstance(resp, str):
        return jsonify({"ok": True, "text": resp}), 200
    return jsonify({"ok": True, "text": str(resp)}), 200


# --------------------------- New alias endpoints -----------------------------
# These allow the UI to call different route names without 404s.
@conversation_bp.route("/orchestrator", methods=["POST", "OPTIONS"])
@conversation_bp.route("/orchestrate", methods=["POST", "OPTIONS"])
@conversation_bp.route("/conversation", methods=["POST", "OPTIONS"])
def chat_orchestrator_alias():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True, silent=True) or {}
    user_text = (data.get("message") or data.get("text") or data.get("prompt") or "").strip()
    history = data.get("history") or data.get("messages") or []

    if not user_text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    try:
        resp = generate_response(user_text=user_text, history=history)
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

    if isinstance(resp, dict) and "text" in resp:
        return jsonify({"ok": True, **resp}), 200
    if isinstance(resp, str):
        return jsonify({"ok": True, "text": resp}), 200
    return jsonify({"ok": True, "text": str(resp)}), 200
