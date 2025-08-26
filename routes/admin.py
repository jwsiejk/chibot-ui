# routes/admin.py
from flask import Blueprint, Response, render_template_string, session
import os, json
from utils.call_log import call_log

_HTML = """<!doctype html>
<meta charset="utf-8"/>
<title>Ask Chip — Admin Call Log</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:#0f1115;color:#eaeef2;margin:0}
header{padding:12px 16px;border-bottom:1px solid #222}
main{padding:12px 16px}
.status{font-size:12px;opacity:.8}
pre{background:#0a0c10;border:1px solid #222;padding:12px;border-radius:8px;max-height:70vh;overflow:auto}
small{opacity:.7}
</style>
<header>
  <strong>Admin Call Log</strong>
  <span class="status" id="status">connecting…</span>
</header>
<main>
  <pre id="log"></pre>
  <p><small>Streams live events via <code>…/stream</code>.</small></p>
</main>
<script>
(function(){
  const out = document.getElementById('log');
  const status = document.getElementById('status');
  function line(s){ out.textContent += s + "\n"; out.scrollTop = out.scrollHeight; }
  function connect(){
    const es = new EventSource('stream');
    status.textContent = 'connected';
    es.onmessage = (ev)=>{
      try{
        const obj = JSON.parse(ev.data);
        line(new Date().toISOString() + '  ' + JSON.stringify(obj));
      }catch(e){ line('! bad event: ' + ev.data); }
    };
    es.onerror = ()=>{ status.textContent = 'disconnected — retrying…'; es.close(); setTimeout(connect, 1200); };
  }
  connect();
})();
</script>
"""

def _is_admin(session_obj) -> bool:
    admins = [e.strip().lower() for e in (os.getenv("ADMIN_EMAILS") or "").split(",") if e.strip()]
    if not admins:
        return True
    user_email = (session_obj.get("user", {}) or {}).get("email") or session_obj.get("email") or ""
    return (user_email or "").lower() in admins

def create_admin_blueprint(name: str = "admin_bp"):
    """Factory: returns a blueprint that serves / (HTML) and /stream (SSE).
    We intentionally export this factory to allow apps to register it under
    multiple prefixes (e.g., /admin and /api/admin) with unique names.
    """
    bp = Blueprint(name, __name__)

    @bp.route("/", methods=["GET"])
    def admin_index():
        if not _is_admin(session):
            return ("Forbidden (not in ADMIN_EMAILS)", 403)
        return render_template_string(_HTML)

    @bp.route("/stream", methods=["GET"])
    def stream():
        def gen():
            q = call_log.subscribe()
            try:
                while True:
                    item = q.get()
                    yield f"data: {json.dumps(item)}\n\n"
            except GeneratorExit:
                pass
            finally:
                call_log.unsubscribe(q)
        return Response(gen(), mimetype="text/event-stream")

    return bp

# Backwards compatibility: some apps import `admin_bp` directly.
admin_bp = create_admin_blueprint("admin_bp")
