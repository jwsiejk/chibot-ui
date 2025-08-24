import os
import sys
import json
import base64
import logging
import inspect
from datetime import datetime
import time
from dataclasses import dataclass, field
from typing import Dict, List

from flask import (
    Flask, request, session, jsonify, render_template,
    url_for, Response, stream_with_context
)

# --------------------------- Conversation State Helpers ---------------------------
def _state_json(ss: _SessionState) -> dict:
    return {
        "product": ss.product,
        "account": ss.account,
        "goal": ss.goal,
        "constraints": ss.constraints,
        "decisions": ss.decisions,
        "next_step": ss.next_step
    }

def _inject_state_and_summary(ss: _SessionState, hist: List[dict]) -> List[dict]:
    """Prepend system messages for pinned state and running summary before raw history."""
    prefix = []
    try:
        st = json.dumps(_state_json(ss), ensure_ascii=False)
        prefix.append({"role": "system", "content": f"SESSION_STATE: {st}"})
    except Exception:
        prefix.append({"role": "system", "content": "SESSION_STATE: {}"})
    if ss.running_summary:
        prefix.append({"role": "system", "content": f"RUNNING_SUMMARY: {ss.running_summary}"})
    return prefix + (hist or [])

def _llm_update_state(ss: _SessionState, user_text: str, assistant_text: str, hist: List[dict]):
    """Ask the LLM to refresh pinned state fields from the latest turn. No canned text emitted to user."""
    try:
        from services.llm_service import generate_reply
    except Exception:
        return
    prior = _state_json(ss)
    prompt = (
        "Update the session state JSON with keys: product, account, goal, constraints, decisions, next_step.
"
        "Use only brief, plain phrases. Keep values if unchanged.
"
        "Return ONLY a JSON object, nothing else.

"
        f"PRIOR_STATE: {json.dumps(prior, ensure_ascii=False)}
"
        f"USER: {user_text}
ASSISTANT: {assistant_text}"
    )
    try:
        updated = generate_reply(messages=[{"role":"user","content": prompt}], max_tokens=160, temperature=0.2)
        if not updated: return
        data = json.loads(updated)
        ss.product = str(data.get("product") or ss.product or "")
        ss.account = str(data.get("account") or ss.account or "")
        ss.goal = str(data.get("goal") or ss.goal or "")
        ss.constraints = str(data.get("constraints") or ss.constraints or "")
        ss.decisions = str(data.get("decisions") or ss.decisions or "")
        ss.next_step = str(data.get("next_step") or ss.next_step or "")
    except Exception:
        pass

def _llm_update_summary(ss: _SessionState, email: str):
    """Refresh running_summary every few turns: 5–7 short spoken lines; no bullets/numbers."""
    try:
        from services.llm_service import generate_reply
    except Exception:
        return
    try:
        hist = memory.get_recent_conversation(user, limit=10) if hasattr(memory, "get_recent_conversation") else [], limit=10) if hasattr(memory, "get_recent_conversation") else []
    aug_hist = _inject_state_and_summary(ss, hist or [])
    resp = generate_response(user_text=text, history=aug_hist)
        reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()

        if not reply:
            yield 'event: done\ndata: {}\n\n'
            return

        import re as _re
        chunks = _re.split(r'(?<=[.!?])\s+', reply)
        for c in chunks:
            if not c:
                continue
            if _was_cancelled(user, started):
                yield 'event: interrupted\ndata: {}\n\n'
                return
            yield 'event: token\ndata: ' + json.dumps({"delta": c + " "}) + '\n\n'
            time.sleep(0.05)

        yield 'event: done\ndata: {}\n\n'

    return Response(stream_with_context(_events()), mimetype="text/event-stream")
# --- END: server-side cancel + SSE chat stream ---

# Orchestrator health alias (kept)
@app.route("/api/orchestrator/health", methods=["GET"])
def api_orchestrator_health():
    return api_health()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
