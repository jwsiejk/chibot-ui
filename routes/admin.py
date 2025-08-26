# routes/admin.py
from flask import Blueprint, Response, render_template_string, session
import os, json
from utils.call_log import call_log

admin_bp = Blueprint("admin_bp", __name__)

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
  <p><small>Streaming from <code>/admin/stream</code>. If your SPA captures <code>/admin</code>, open <code>/tools/admin-log</code> instead.</small></p>
</main>
<script>
(function(){
  const out = document.getElementById('log');
  const status = document.getElementById('status');
  function line(s){ out.textContent += s + "\n"; out.scrollTop = out.scrollHeight; }
  function connect(){
    const es = new EventSource('/admin/stream');
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

def _is_admin() -> bool:
    admins = [e.strip().lower() for e in (os.getenv("ADMIN_EMAILS") or "").split(",") if e.strip()]
    if not admins:
        return True
    user_email = (session.get("user", {}) or {}).get("email") or session.get("email") or ""
    return (user_email or "").lower() in admins

@admin_bp.route("/", methods=["GET"])
def admin_index():
    if not _is_admin():
        return ("Forbidden (not in ADMIN_EMAILS)", 403)
    return render_template_string(_HTML)

@admin_bp.route("/stream", methods=["GET"])
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
