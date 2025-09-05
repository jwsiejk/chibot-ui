# app/__init__.py
from flask import Flask, render_template
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .middleware.rate_limit import register_before_request as rate_limit_register

def create_app():
    # Serve /static (JS/CSS/img) and /templates
    app = Flask(__name__, static_folder='../static', static_url_path='/static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False

    # Secret key (read env in production)
    import os
    app.secret_key = os.environ.get("SECRET_KEY","dev-secret-change-me")

    # Register v1-only API
    bp = create_v1_blueprint()
    app.register_blueprint(bp, url_prefix="/api/v1")

    # Basic pages
    @app.get("/")
    def index(): return render_template("index.html")

    # Middleware
    app.before_request(csrf_before_request)
    rate_limit_register(app)
    return app
