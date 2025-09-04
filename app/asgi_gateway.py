from __future__ import annotations
import typing as _t
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from flask import Flask, Response, request
from app.factory import create_app

# The HTTP (Flask) app
flask_app: Flask = create_app()

# Simple ASGI adapter for Gunicorn/Uvicorn to serve the Flask app for HTTP.
# WS endpoint is declared but returns 501 here; will be implemented in Phase 2.
# Uvicorn can serve Flask (WSGI) via its WSGIMiddleware automatically.
asgi = flask_app  # Uvicorn understands WSGI via WSGIMiddleware by default.
