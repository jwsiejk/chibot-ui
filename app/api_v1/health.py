from flask import Blueprint, jsonify

bp = Blueprint("health_v1", __name__, url_prefix="/api/v1")

@bp.get("/health")
def health():
    return jsonify(ok=True, checks={"app":"up"}), 200
