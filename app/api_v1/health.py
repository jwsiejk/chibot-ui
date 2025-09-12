# app/api_v1/health.py
from flask import Blueprint, jsonify

bp = Blueprint("health_v1", __name__, url_prefix="/api/v1")

@bp.get("/health")
def health():
    # Fast, dependency-free liveness. Attach deeper checks behind flags if needed.
    return jsonify(ok=True, checks={"app":"up"}), 200
