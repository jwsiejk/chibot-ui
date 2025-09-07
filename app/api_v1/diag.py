
from flask import Blueprint, jsonify
import os
from ..db import db
from ..services.tts_provider import get_tts_provider_name

bp = Blueprint("diag", __name__)

@bp.get("/_diag/tts")
def diag_tts():
    cfg = db.get_config()
    resolved = get_tts_provider_name(cfg)
    return jsonify({
        "ok": True,
        "resolved_tts_provider": resolved,
        "cfg_tts_provider": cfg.get("tts_provider", "auto"),
        "env": {
            "CI_FAST": os.environ.get("CI_FAST"),
            "ELEVENLABS_API_KEY_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "OPENAI_API_KEY_present": bool(os.environ.get("OPENAI_API_KEY"))
        }
    })
