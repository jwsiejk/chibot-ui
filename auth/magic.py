from flask import Blueprint, request, redirect, url_for, session, render_template
from itsdangerous import URLSafeTimedSerializer
from flask_mail import Message

bp = Blueprint('magic_auth', __name__)
serializer = URLSafeTimedSerializer('super-secret-key')  # Replace with real key

@bp.route('/login/magic', methods=['GET', 'POST'])
def request_link():
    if request.method == 'POST':
        email = request.form['email']
        token = serializer.dumps(email, salt='magic-link')
        link = url_for('magic_auth.login_with_token', token=token, _external=True)
        # send link via email
        print(f"Magic link: {link}")  # replace with Flask-Mail
        return "Check your email for a login link."
    return render_template('login_magic.html')

@bp.route('/login/magic/<token>')
def login_with_token(token):
    try:
        email = serializer.loads(token, salt='magic-link', max_age=600)
    except Exception:
        return "Invalid or expired token", 403
    session['user_id'] = email
    return redirect('/')
