from flask import Flask
from .api_v1 import create_v1_blueprint
from .middleware.csrf import csrf_before_request
from .ws.chat_ws import register_ws_route

def create_app():
    app = Flask(__name__, static_folder='../static', template_folder='../templates')
    app.config['JSON_SORT_KEYS'] = False
    app.secret_key = "test-secret"
    app.register_blueprint(create_v1_blueprint(), url_prefix="/api/v1")
    app.before_request(csrf_before_request)
    register_ws_route(app)
    return app
