
from __future__ import annotations
import json, os, anyio
from typing import Optional
from flask import Flask, request, jsonify
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute, Mount
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware

from .config_store import get_config, set_config
from .ws_bus import BUS

# Flask app for HTTP v1 APIs
flask_app = Flask(__name__)


@flask_app.get("/admin")
def admin_page():
    return """
<!doctype html><html><head><meta charset='utf-8'><title>Admin Config</title></head>
<body style="font-family: system-ui; padding:20px">
<h2>Streaming ASR (Deepgram)</h2>
<form id="f">
<label>stt_mode:
  <select name="stt_mode">
    <option value="batch">batch</option>
    <option value="stream">stream</option>
  </select>
</label><br/>
<label>model: <input name="model" value="nova-3"></label><br/>
<label>language: <input name="language" value="en"></label><br/>
<label>smart_format: <input type="checkbox" name="smart_format" checked></label><br/>
<label>listen_url: <input name="listen_url" size="60" value="wss://api.deepgram.com/v1/listen"></label><br/>
<label>encoding: <input name="encoding" value="opus"></label><br/>
<label>sample_rate: <input name="sample_rate" value="48000"></label><br/>
<label>interim_results: <input type="checkbox" name="interim_results" checked></label><br/>
<button type="submit">Save</button>
</form>
<pre id="out"></pre>
<script>
async function load() {
  const r = await fetch('/api/v1/admin/config');
  const cfg = await r.json();
  document.querySelector('[name=stt_mode]').value = cfg.stt_mode;
  const dg = cfg.deepgram;
  for (const k of ['model','language','listen_url','encoding','sample_rate']) {
    const el = document.querySelector('[name='+k+']'); if (el) el.value = dg[k];
  }
  document.querySelector('[name=smart_format]').checked = !!dg.smart_format;
  document.querySelector('[name=interim_results]').checked = !!dg.interim_results;
}
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const payload = {
    stt_mode: form.get('stt_mode'),
    deepgram: {
      model: form.get('model'),
      language: form.get('language'),
      smart_format: !!document.querySelector('[name=smart_format]').checked,
      listen_url: form.get('listen_url'),
      encoding: form.get('encoding'),
      sample_rate: parseInt(form.get('sample_rate')),
      interim_results: !!document.querySelector('[name=interim_results]').checked
    }
  };
  const r = await fetch('/api/v1/admin/config', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  document.getElementById('out').textContent = JSON.stringify(j, null, 2);
};
load();
</script>
</body></html>
"""

@flask_app.get("/api/v1/admin/config")
def get_admin_config():
    return jsonify(get_config())

def _validate_config(payload: dict):
    errors = {}
    dg = payload.get("deepgram")
    if dg:
        lu = dg.get("listen_url")
        if lu and not str(lu).startswith("wss://"):
            errors["listen_url"] = "listen_url must start with wss://"
        if "sample_rate" in dg and dg["sample_rate"] != 48000:
            errors["sample_rate"] = "sample_rate must be 48000"
        if "encoding" in dg and dg["encoding"] != "opus":
            errors["encoding"] = "encoding must be 'opus'"
    stt_mode = payload.get("stt_mode")
    if stt_mode and stt_mode not in ("batch", "stream"):
        errors["stt_mode"] = "stt_mode must be 'batch' or 'stream'"
    return errors

@flask_app.post("/api/v1/admin/config")
def post_admin_config():
    data = request.json or {}
    errs = _validate_config(data)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400
    if "stt_mode" in data:
        set_config("stt_mode", data["stt_mode"])
    if "deepgram" in data:
        set_config("deepgram", data["deepgram"])
    return jsonify({"ok": True, "config": get_config()})


@flask_app.get("/api/v1/_test/events")
def get_test_events():
    sess = request.args.get("session_id", "default")
    from .ws_bus import BUS
    # NOTE: direct access; safe in tests
    hist = BUS._history.get(sess, [])[:]
    BUS._history[sess] = []
    return jsonify(hist)

@flask_app.post("/api/v1/voice/stt/stream")
def post_stt_stream():
    sess = request.args.get("session_id") or request.form.get("session_id") or "default"
    chunk = None
    data = b""
    try:
        chunk = request.files.get("chunk")
    except Exception:
        chunk = None
    if chunk:
        data = chunk.read()
    else:
        data = request.get_data(cache=False) or b""
    if not data:
        return jsonify({"error": "missing chunk"}), 400
    if len(data) > 512 * 1024:
        return jsonify({"error": "chunk too large"}), 413
    from .services.streaming_asr.stream_manager import get_manager
    mgr = get_manager()
    mgr.enqueue(sess, data)
    return jsonify({"ok": True})

# Starlette for WS

async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    query = dict(websocket.query_params)
    session_id = query.get("session_id", "default")
    q = await BUS.subscribe(session_id)
    try:
        while True:
            item = await q.get()
            await websocket.send_json(item)
    except WebSocketDisconnect:
        await BUS.unsubscribe(session_id, q)

starlette_app = Starlette(routes=[
    WebSocketRoute("/ws/v1/chat", ws_endpoint),
])


# Compose ASGI
from starlette.applications import Starlette as _Star
asgi = _Star()
asgi.add_websocket_route("/ws/v1/chat", ws_endpoint)
asgi.mount("/", app=WSGIMiddleware(flask_app))
asgi.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
