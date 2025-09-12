
from flask import Blueprint, jsonify
from ..services.admin_config import get_admin_config

bp = Blueprint("voice_mode_v1", __name__, url_prefix="/api/v1/voice")

@bp.get("/stt-mode")
def stt_mode():
    cfg = get_admin_config()
    mode = cfg.get("stt_mode", "batch")
    return jsonify(stt_mode=mode), 200
