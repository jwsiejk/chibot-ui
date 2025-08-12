print("✅ Chip app starting...")

import os
if os.getenv("DISABLE_EVENTLET", "0") != "1":
    import eventlet  # type: ignore
    eventlet.monkey_patch(all=True)

# --- Normalize DATABASE_URL before importing anything that might use it ---
_raw_db = (os.getenv("DATABASE_URL") or "").strip()
if (_raw_db.startswith('"') and _raw_db.endswith('"')) or (_raw_db.startswith("'") and _raw_db.endswith("'")):
    _raw_db = _raw_db[1:-1].strip()
os.environ["DATABASE_URL"] = _raw_db.replace("\n", "").replace("\r", "").replace(" ", "")

# ---------------- standard imports ----------------
import json
import traceback
import re
import base64
import mimetypes
import threading
from threading import Lock
import requests
from uuid import uuid4
from datetime import datetime
from urllib.parse import urlparse, quote_plus
from io import BytesIO

from flask import (
    Flask, request, jsonify, render_template, session, Response,
    stream_with_context, send_file, redirect
)
from flask_session import Session
from werkzeug.utils import secure_filename

# --- websocket support ---
from flask_sock import Sock

# --- vendor clients ---
from elevenlabs.client import ElevenLabs
from openai import OpenAI
import httpx

import psycopg2
import psycopg2.extras as extras  # for RealDictCursor

# --- optional deps with guards ---
try:
    from openpyxl import load_workbook
    HAS_OPENPYXL = True
except Exception:
    HAS_OPENPYXL = False

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False

try:
    from pptx import Presentation
    HAS_PPTX = True
except Exception:
    HAS_PPTX = False

# --- email (Gmail SMTP) ---
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# only import memory after DATABASE_URL is sanitized
from memory import get_user, save_user, log_conversation, get_connection

# -----------------------------------------------------------------------------
# Flask & sessions
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("FLASK_SECRET") or "supersecret"
app.config["SESSION_TYPE"] = "filesystem"
app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
if os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes"):
    app.config["SESSION_COOKIE_SECURE"] = True
Session(app)

# WebSocket sock (adds /ws/* routes we define below)
sock = Sock(app)

# -----------------------------------------------------------------------------
# Global clients & feature flags
# -----------------------------------------------------------------------------
voice_id = os.getenv("CHIP_VOICE_ID")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in ("1", "true", "yes")
ELEVEN_MODEL_ID = os.getenv("ELEVEN_MODEL_ID", "eleven_multilingual_v2")
ELEVEN_OUTPUT_FORMAT = os.getenv("ELEVEN_OUTPUT_FORMAT", "mp3_22050_32")
ELEVEN_STREAM_LATENCY = os.getenv("ELEVEN_STREAM_LATENCY", "0")

# OpenAI client
oai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# -----------------------------------------------------------------------------
# Admins (env-driven; default to your address)
# -----------------------------------------------------------------------------
ADMIN_EMAILS = {
    e.strip().lower()
    for e in (os.getenv("ADMIN_EMAILS") or "jwsiejk@purestorage.com").split(",")
    if e.strip()
}

def _is_admin(email: str) -> bool:
    return (email or "").strip().lower() in ADMIN_EMAILS

# -----------------------------------------------------------------------------
# Persona loader (externalized under static/chip/persona.txt)
# -----------------------------------------------------------------------------
_DEFAULT_PERSONA = (
    "You are Chip, a virtual Pure Storage solution engineer.\n"
    "Tone: Nebraska plain-spoken, warm, practical. Use natural contractions. "
    "Use gentle hedges sparingly (“looks like”, “roughly”). One light colloquialism every few turns at most.\n"
    "Brevity: Default to ~20 words unless the user asks for more. If they say “more”, expand naturally.\n"
    "Helpfulness: Answer directly first. Offer a follow-up only when it truly helps "
    "(ambiguity, likely next step, or the user seems stuck). Otherwise keep quiet.\n"
    "Small talk: If the user mixes casual remarks (e.g., weather, greetings) with a question), "
    "start with one short friendly clause acknowledging it, then pivot to the answer.\n"
    "Guardrails: Do not invent data. If unsure, say so and propose the next action. "
    "Stay professional; no sarcasm or slang overload.\n"
    "Closers (occasionally, when appropriate): “Want me to dig deeper?”, "
    "“Need a quick example?”, “Should I pull the numbers behind that?”, "
    "“I can check related items if you want.”"
)

_PERSONA_CACHE = {"text": None, "mtime": 0, "path": None}

def _persona_path() -> str:
    env_path = os.getenv("CHIP_PERSONA_PATH")
    if env_path:
        return env_path
    return os.path.join(app.root_path, "static", "chip", "persona.txt")

def load_persona() -> str:
    try:
        path = _persona_path()
        _PERSONA_CACHE["path"] = path
        st = os.stat(path)
        if st.st_mtime != _PERSONA_CACHE["mtime"]:
            with open(path, "r", encoding="utf-8") as f:
                _PERSONA_CACHE["text"] = f.read().strip()
            _PERSONA_CACHE["mtime"] = st.st_mtime
        return _PERSONA_CACHE["text"] or _DEFAULT_PERSONA
    except Exception:
        return _DEFAULT_PERSONA

# -----------------------------------------------------------------------------
# Weather-aware small-talk helper
# -----------------------------------------------------------------------------
_OMAHA_LAT = 41.2565
_OMAHA_LON = -95.9345
_SMALLTALK_WEATHER_RE = re.compile(
    r"\b(weather|outside|nice out|nice outside|sunny|cloudy|rain(y)?|snow|snowing|windy|hot|cold|freezing|chilly|beautiful day)\b",
    re.IGNORECASE
)

def _fetch_omaha_temp_f(timeout=3.5):
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": _OMAHA_LAT,
            "longitude": _OMAHA_LON,
            "current": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
        }
        r = httpx.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        js = r.json() or {}
        cur = js.get("current") or {}
        t = cur.get("temperature_2m")
        return float(t) if isinstance(t, (int, float)) else None
    except Exception as e:
        print("ℹ️ Omaha weather lookup failed:", str(e))
        return None

def _temp_feel_phrase(t_f):
    if t_f is None:
        return None
    t = float(t_f)
    if t <= 35:  feel = "cold"
    elif t <= 50: feel = "chilly"
    elif t <= 70: feel = "mild"
    elif t <= 85: feel = "warm"
    else:        feel = "hot"
    return f"{feel} here in Omaha ({round(t)}°F)"

def _smalltalk_context_if_any(user_text: str, name: str):
    if not user_text or not _SMALLTALK_WEATHER_RE.search(user_text):
        return None
    t = _fetch_omaha_temp_f()
    feel = _temp_feel_phrase(t)
    if feel:
        return {
            "role": "system",
            "content": (
                f"User mentioned weather in passing. Start your reply with one short, friendly clause "
                f"acknowledging them by name ({name}) and briefly noting Omaha conditions: “{feel}”. "
                f"Then pivot to the answer."
            )
        }
    else:
        return {
            "role": "system",
            "content": (
                f"User mentioned weather in passing. Start with one short friendly clause acknowledging it by name ({name}), "
                f"then pivot to the answer. Keep it concise."
            )
        }

# -----------------------------------------------------------------------------
# Helpers, DB functions, route definitions...
# -----------------------------------------------------------------------------
# (Keeping everything else exactly as in your original file — unchanged)

# -----------------------------------------------------------------------------
# Register blueprints for chat & voice
# -----------------------------------------------------------------------------
from chat_routes import create_chat_blueprint
from voice_routes import create_voice_blueprint
from services.intents import parse_email_intent  # added

deps = {
    "oai": oai,
    "eleven": eleven,
    "voice_id": voice_id,
    "TTS_ENABLED": TTS_ENABLED,
    "generate_chip_response": generate_chip_response,
    "find_account_row": find_account_row,
    "repo_search": repo_search,
    "parse_email_intent": parse_email_intent,  # added so chat_routes gets it
}

app.register_blueprint(create_chat_blueprint(deps))
app.register_blueprint(create_voice_blueprint(deps))

# -----------------------------------------------------------------------------
# (Rest of your file continues unchanged from here — greet route, websocket, email endpoints, etc.)
