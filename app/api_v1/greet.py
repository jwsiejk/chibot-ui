from __future__ import annotations
from flask import Blueprint, jsonify

bp = Blueprint("greet_v1", __name__)

@bp.get("/greet")
def greet():
    # Phase 1 will implement: queue assistant turn and stream over WS.
    return jsonify(ok=False, error="not_implemented"), 501
