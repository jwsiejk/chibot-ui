# app/__init__.py
import os
from flask import Flask, render_template, send_from_directory
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .middleware.rate_limit import register_before_request as rate_limit_register

def create_app():
    # Serve /static (JS/CSS/img) and /templates
    app = Flask(__name__, static_folder='../static', static_url_path='/static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False

    # Secret key (read env in production)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Register v1-only API
    bp = create_v1_blueprint()
    app.register_blueprint(bp, url_prefix="/api/v1")

    # Basic pages
    @app.get("/")
    def index():
        return render_template("index.html")

    # Favicon (browsers request /favicon.ico by default)
    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")

    # Middleware
    app.before_request(csrf_before_request)
    rate_limit_register(app)
    return app
