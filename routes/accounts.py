# routes/accounts.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from utils.call_log import call_log

try:
    from services.accounts_service import search_accounts
except Exception:
    def search_accounts(q: str, limit: int = 20):  # type: ignore
        return []

accounts_bp = Blueprint("accounts_bp", __name__, url_prefix="/api")

@accounts_bp.get("/accounts/search")
def accounts_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        call_log.add("accounts", "empty_query")
        return jsonify({"ok": True, "results": []})
    try:
        results = search_accounts(q, limit=int(request.args.get("limit") or 20))
    except Exception as e:
        call_log.add("accounts", "search_error", error=str(e))
        results = []
    call_log.add("accounts", "search_ok", q=q, n=len(results))
    return jsonify({"ok": True, "results": results})
