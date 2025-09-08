# app/api_v1/health.py
from flask import Blueprint, jsonify

bp = Blueprint("health_v1", __name__, url_prefix="/api/v1")

@bp.get("/health")
def health():
    return jsonify(ok=True, status="ok"), 200

@bp.head("/health")
def health_head():
    return ("", 200)
