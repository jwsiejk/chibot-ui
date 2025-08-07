import os
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, redirect, url_for, session

bp = Blueprint('google_auth', __name__)
oauth = OAuth()

def init_oauth(app):
    oauth.init_app(app)
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
    app.config['GOOGLE_DISCOVERY_URL'] = "https://accounts.google.com/.well-known/openid-configuration"

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
        client_kwargs={'scope': 'openid email profile'}
    )

@bp.route('/login/google')
def login_google():
    redirect_uri = url_for('google_auth.auth_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@bp.route('/auth/google/callback')
def auth_callback():
    token = oauth.google.authorize_access_token()
    user_info = token['userinfo']
    email = user_info['email']

    if not email.endswith('@purestorage.com'):
        return "Access denied", 403

    session['user_id'] = email
    session['user_name'] = user_info.get('name')
    return redirect('/')
