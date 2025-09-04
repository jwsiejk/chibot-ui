from __future__ import annotations
from flask import Flask, jsonify

def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(e):
        return jsonify(ok=False, error="not_found"), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(ok=False, error="server_error"), 500
