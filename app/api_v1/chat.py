from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
from ..services.mock_emailer import send_transcript
from ..services.streaming import make_assistant_frames, schedule_frames
from ..ws.bus import bus
bp = Blueprint("chat", __name__)
@bp.post("")
def chat():
    data=request.get_json(silent=True) or {}
    sid=data.get("session_id","default"); cmd=data.get("cmd"); email=get_user()
    if cmd=="interrupt":
        tid=data.get("turn_id"); bus.cancel_turn(sid, tid); bus.broadcast(sid, {"type":"state","phase":"ready"}); return jsonify({"ok": True, "interrupted": True})
    if cmd=="nudge":
        s = db.memory['sessions'].setdefault(sid, {'email': email, 'messages': [], 'nudges': 0, 'persona_id':'chip'}); s['nudges']+=1
        if s['nudges']<=2:
            tid, frames = make_assistant_frames("Still with me? Want a quick recap?")
            for fr in frames:
                if fr.get("type")=="end": fr["reason"]="nudge"
            schedule_frames(sid, frames); return jsonify({"ok": True, "nudged": True, "count": s['nudges']})
        return jsonify({"ok": True, "nudged": False, "count": s['nudges']})
    if cmd=="end_session":
        body=db.get_transcript(sid); send_transcript(email,"Ask Chip — Session transcript",body); return jsonify({"ok": True, "emailed": True})
    text=(data.get("text") or "").strip()
    if text: db.ensure_session(sid, email); db.add_message(sid, "user", text)
    tid, frames = make_assistant_frames(text or "chat"); schedule_frames(sid, frames)
    return jsonify({"ok": True, "turn_id": tid})
