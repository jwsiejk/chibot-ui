from __future__ import annotations

from flask import Blueprint, jsonify

from app.policy.loader import load_policy

bp = Blueprint("policy", __name__)


@bp.get("/policy/effective")
def get_effective_policy():
    policy = load_policy()
    return jsonify(policy)
