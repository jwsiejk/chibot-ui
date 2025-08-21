# wsgi.py
# Minimal WSGI entrypoint for Gunicorn.

from server import create_app

# Gunicorn looks for a module-level variable named `app`
app = create_app()

if __name__ == "__main__":
    # For local testing only; Gunicorn ignores this block.
    app.run(host="0.0.0.0", port=5000)
