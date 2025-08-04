from flask import Flask, render_template, request, redirect, url_for
from memory import init_db, get_user, save_user, log_conversation
import os

app = Flask(__name__)

# Initialize the database
init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/3d', methods=['GET', 'POST'])
def index_3d():
    if request.method == 'POST':
        user_input = request.form.get('user_input')
        print(f"[3D] User asked: {user_input}")
        # Future: You can route this to Chip’s brain here
        return redirect(url_for('index_3d'))
    return render_template('index_3d.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
