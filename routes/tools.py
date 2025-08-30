# routes/tools.py
from flask import Blueprint, Response

tools_bp = Blueprint("tools_bp", __name__)

_DIAG = """<!doctype html>
<meta charset="utf-8"><title>Ask Chip — Diagnostics</title>
<style>body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:#0f1115;color:#eaeef2;margin:0}main{padding:16px}.card{border:1px solid #222;background:#0a0c10;border-radius:10px;padding:12px;margin:12px 0}code{background:#11161c;padding:2px 4px;border-radius:4px}.ok{color:#9fda9b}.bad{color:#ff9b9b}</style>
<main>
  <h2>Diagnostics</h2>
  <div class="card"><strong>Health</strong><pre id="health">…</pre></div>
  <div class="card">
    <strong>TTS Smoke Test</strong>
    <button id="btn">Speak test line</button>
    <pre id="tts">…</pre>
  </div>
</main>
<script>
async function getJson(paths){
  for (const p of paths){
    try{ const r = await fetch(p); if (r.ok){ return await r.json(); } }catch(e){}
  }
  throw new Error('none of ' + paths.join(', ') + ' responded');
}
(async()=>{
  try{
    const h = await getJson(['/api/health','/health','/api/voice/health','/api/openai/health']);
    document.getElementById('health').textContent = JSON.stringify(h, null, 2);
  }catch(e){
    document.getElementById('health').textContent = 'health check failed: ' + e;
  }
})();
document.getElementById('btn').onclick = async ()=>{
  const pre = document.getElementById('tts');
  try{
    const r = await fetch('/api/voice/tts_with_visemes', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: 'Diagnostics test. If you hear me, voice works.' })
    }).then(r=>r.json());
    pre.textContent = JSON.stringify(r, null, 2);
    if (r && r.ok && (r.audio || r.audio_base64)){
      const a = new Audio('data:audio/mpeg;base64,' + (r.audio || r.audio_base64));
      a.play();
    }
  }catch(e){
    pre.textContent = 'TTS failed: ' + e;
  }
};
</script>"""

_ADMIN = """<!doctype html>
<meta charset="utf-8"><title>Ask Chip — Admin Log (Static Tool)</title>
<style>body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:#0f1115;color:#eaeef2;margin:0}header{padding:12px 16px;border-bottom:1px solid #222}main{padding:12px 16px}.status{font-size:12px;opacity:.8}pre{background:#0a0c10;border:1px solid #222;padding:12px;border-radius:8px;max-height:72vh;overflow:auto}small{opacity:.7}a{color:#7cb1ff}</style>
<header><strong>Admin Log (Static Tool)</strong><span class="status" id="status">connecting…</span></header>
<main><pre id="log"></pre><p><small>Tries <code>/admin/stream</code> then <code>/api/admin/stream</code>.</small></p></main>
<script>
(function(){
  const out = document.getElementById('log');
  const status = document.getElementById('status');
  function line(s){ out.textContent += s + "\n"; out.scrollTop = out.scrollHeight; }
  function connect(path){
    const es = new EventSource(path);
    status.textContent = 'connected to ' + path;
    es.onmessage = (ev)=>{
      try{
        const obj = JSON.parse(ev.data);
        line(new Date().toISOString() + '  ' + JSON.stringify(obj));
      }catch(e){ line('! bad event: ' + ev.data); }
    };
    es.onerror = ()=>{ status.textContent = 'disconnected'; es.close(); fallback(path); };
  }
  function fallback(prev){
    if (prev === '/admin/stream'){ setTimeout(()=>connect('/api/admin/stream'), 800); }
    else { status.textContent = 'disconnected (both paths failed)'; }
  }
  connect('/admin/stream');
})();
</script>"""

@tools_bp.route('/askchip-diagnostics.html', methods=['GET'])
def diagnostics_html():
    return Response(_DIAG, content_type='text/html; charset=utf-8')

@tools_bp.route('/admin-log.html', methods=['GET'])
def admin_log_html():
    return Response(_ADMIN, content_type='text/html; charset=utf-8')


@tools_bp.route('/api/version', methods=['GET'])
def api_version():
    try:
        import flask
        from flask import current_app as app
        rules = sorted(str(r.rule) for r in app.url_map.iter_rules())
    except Exception:
        rules = []
    try:
        import pkgutil, sys, hashlib
        # Compute a quick signature of critical files to ensure deployment freshness
        import os
        base = os.path.dirname(os.path.dirname(__file__))
        important = [
            os.path.join(base, 'templates', 'index.html'),
            os.path.join(base, 'routes', 'chat.py'),
            os.path.join(base, 'app', 'legacy_app.py'),
        ]
        h = hashlib.sha256()
        for p in important:
            try:
                with open(p, 'rb') as f:
                    h.update(f.read())
            except Exception:
                pass
        sig = h.hexdigest()[:16]
    except Exception:
        sig = 'na'
    return Response(
        'ok\n' + '\n'.join(rules) + '\nSIG:' + sig + '\n',
        mimetype='text/plain'
    )
