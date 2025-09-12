# app/api_v1/health.py
from flask import Blueprint, jsonify

bp = Blueprint("health_v1", __name__, url_prefix="/api/v1")

@bp.get("/health")
def health():
    # Minimal liveness. Expand with DB/provider checks later if desired.
    return jsonify(ok=True), 200
