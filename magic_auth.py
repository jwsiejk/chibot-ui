from flask import Blueprint, request, session, redirect, url_for, render_template
import uuid

bp = Blueprint('magic_auth', __name__)

# Simulated in-memory "magic link" store
magic_links = {}

def init_magic(app):
    app.config['MAGIC_SECRET_KEY'] = app.config.get('MAGIC_SECRET_KEY', 'replace-me')

@bp.route("/login/magic", methods=["GET", "POST"])
def login_magic():
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            return "Email is required", 400

        # Create a fake "magic" token (in production, use secure UUID/token)
        token = str(uuid.uuid4())
        magic_links[token] = email

        # Simulate sending an email with the link (you'd send real email here)
        magic_url = url_for('magic_auth.magic_callback', token=token, _external=True)
        print(f"[Magic Link] Send this link to the user: {magic_url}")

        return f"Magic link sent to {email} (Check server logs for demo link)"
    
    return render_template("magic_login.html")

@bp.route("/auth/magic/<token>")
def magic_callback(token):
    email = magic_links.get(token)
    if not email:
        return "Invalid or expired magic link", 403

    # Store user session
    session['user_id'] = email
    session['user_name'] = email.split("@")[0].capitalize()

    return redirect("/")
