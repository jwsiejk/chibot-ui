# wsgi.py
# WSGI entrypoint that exposes the Flask app defined in app.py

import os
from app import app  # <-- this is your real app with all routes and UI

if __name__ == "__main__":
    # Local testing only; Render/Gunicorn ignores this block
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
