
// Admin JS expects ensureCSRF; provide alias from window namespace
(function(){ if(window.__askchip && typeof window.__askchip.ensureCSRF==='function' && typeof window.ensureCSRF!=='function'){ window.ensureCSRF = window.__askchip.ensureCSRF; } })();


// --- Admin Safe Mode + Bootstrap ---
(function(){
  try{
    window.ASKCHIP = window.ASKCHIP || { api: {
      logs: '/api/v1/admin/logs',
      config_get: '/api/v1/admin/config',
      config_set: '/api/v1/admin/config/update',
      runtime: '/api/v1/admin/runtime'
    }};
  }catch(e){}
})();
const SAFE_MODE = new URLSearchParams(location.search).has('safe') || new URLSearchParams(location.search).get('s')==='1';
window.ASKCHIP = window.ASKCHIP || { api: { logs:'/api/v1/admin/logs', config_get:'/api/v1/admin/config', config_set:'/api/v1/admin/config/update', layout_get:'/api/v1/admin/layouts', layout_set:'/api/v1/admin/layouts', runtime:'/api/v1/admin/runtime' } };
// static/js/admin.js
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* Tabs */
(function tabs(){
  const ts = $$(".tab");
  const panels = [$("#tab-logs"), $("#tab-runtime"), $("#tab-config"), $("#tab-users")];
  ts.forEach(t => t.addEventListener("click", () => {
    ts.forEach(x => x.setAttribute("aria-selected", "false"));
    t.setAttribute("aria-selected", "true");
    panels.forEach(p => p.setAttribute("aria-hidden", "true"));
    const target = document.getElementById(t.getAttribute("aria-controls"));
    if (target) target.setAttribute("aria-hidden", "false");
  }));
})();

/* Live Logs (polling JSON) with filter + clear */
(function logFeed(){
  const baseUrl = window.ASKCHIP.api.logs;
  const el = $("#adminLog");
  if (!el) return;

  let lastStep = 0;
  let active = true;
  let timer = null;
  let failureCount = 0;

  const schedule = (ms) => {
    if (!active) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(run, Math.max(200, ms));
  };

  const drain = async () => {
    const params = new URLSearchParams();
    if (lastStep) params.set('after', String(lastStep));
    const url = params.toString() ? `${baseUrl}?${params.toString()}` : baseUrl;

    try {
      const resp = await fetch(url, { credentials: 'include' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const events = Array.isArray(data?.events) ? data.events : [];

      if (!events.length && !lastStep) {
        lastStep = Number(data?.latest_step || 0) || 0;
      }

      const filter = ($("#logFilter").value || "").toLowerCase();
      for (const evt of events) {
        const step = Number(evt?.step || 0);
        if (step > lastStep) lastStep = step;
        const line = JSON.stringify(evt);
        if (!filter || line.toLowerCase().includes(filter)) {
          el.textContent += `${line}\n`;
        }
      }
      if (events.length) {
        el.scrollTop = el.scrollHeight;
      }

      failureCount = 0;
      return events.length ? 300 : 1200;
    } catch (err) {
      failureCount += 1;
      const message = err?.message || err || 'unknown error';
      el.textContent += `[poll-error] ${message}\n`;
      el.scrollTop = el.scrollHeight;
      return Math.min(5000, 600 * failureCount);
    }
  };

  async function run() {
    if (!active) return;
    const delayMs = await drain();
    schedule(delayMs || 1200);
  }

  $("#logClear").addEventListener("click", () => {
    el.textContent = "";
    lastStep = 0;
  });

  $("#logFilter").addEventListener("input", () => {
    lastStep = 0;  // reload to honour new filter
    el.textContent = '';
  });

  window.addEventListener('beforeunload', () => {
    active = false;
    if (timer) clearTimeout(timer);
  });

  run();
})();

/* Config (dynamic form) */
async function cfgLoad(){
  const url = window.ASKCHIP.api.config_get;
  const box = $("#configForm"), status = $("#cfgStatus");
  try {
    const r = await fetch(url, {credentials:"include"});
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    const j = await r.json();
    const cfg = j.config || j;
    box.innerHTML = "";
    Object.entries(cfg).forEach(([k,v]) => {
      const label = document.createElement("label");
      label.textContent = k;
      const input = document.createElement("input");
      input.dataset.key = k;
      input.value = (typeof v === "object") ? JSON.stringify(v) : String(v);
      label.htmlFor = `cfg-${k}`;
      input.id = `cfg-${k}`;
      box.appendChild(label);
      box.appendChild(input);
    });
    status.textContent = "Loaded.";
  } catch (e) {
    status.textContent = String(e.message || e);
  }
}
async function cfgSave(){
  const url = window.ASKCHIP.api.config_set;
  const box = $("#configForm"), status = $("#cfgStatus");
  const payload = {};
  box.querySelectorAll("input[data-key]").forEach(i => {
    const k = i.dataset.key;
    let v = i.value;
    try {
      if (/^\s*[\{\[].*[\}\]]\s*$/.test(v)) v = JSON.parse(v);
      else if (/^\d+(\.\d+)?$/.test(v)) v = Number(v);
      else if (v === "true" || v === "false") v = (v === "true");
    } catch {}
    payload[k] = v;
  });
  try {
    const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, credentials:"include", body: JSON.stringify(payload)});
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    $("#cfgStatus").textContent = "Saved.";
  } catch (e) {
    $("#cfgStatus").textContent = String(e.message || e);
  }
}
$("#cfgLoad").addEventListener("click", cfgLoad);
$("#cfgSave").addEventListener("click", cfgSave);

/* Layout Editor */
async function lySaveDraft(){
  const status = $("#lyStatus");
  const body = {
    variant: "draft",
    breakpoint: "desktop",
    layout: {
      stage_side: $("#stageSide").value,
      show_instruction_strip: !!$("#showStrip").checked,
      show_state_dots: !!$("#showDots").checked
    }
  };
  try {
    const r = await fetch(/* layout_api_removed */, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "include",
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`POST /admin/config → ${r.status}`);
    const j = await r.json();
    status.textContent = `Draft saved (v${j.version})`;
  } catch (e) {
    status.textContent = String(e.message || e);
  }
}

$("#lyLoadPublished").addEventListener("click", () => lyLoad("published"));
$("#lyLoadDraft").addEventListener("click", () => lyLoad("draft"));
$("#lySaveDraft").addEventListener("click", lySaveDraft);
$("#lyPublish").addEventListener("click", lyPublish);

// Load initial data
cfgLoad();
lyLoad("published");


/* Knowledge Tab */
(function kb(){
  const Q = (id)=>document.getElementById(id);
  const api = {
    list: (q,tag)=> fetch(`/api/v1/admin/kb/docs?query=${encodeURIComponent(q||"")}&tag=${encodeURIComponent(tag||"")}`, {credentials:"include"}).then(r=>r.json()),
    get: (id)=> fetch(`/api/v1/admin/kb/docs/${id}`, {credentials:"include"}).then(r=>r.json()),
    del: (id)=> fetch(`/api/v1/admin/kb/docs/${id}`, {method:"DELETE", credentials:"include", headers: csrf()} ).then(r=>r.json()),
    seed: (title,tags,body)=> fetch(`/api/v1/admin/kb/seed`, {method:"POST", credentials:"include", headers: Object.assign({"Content-Type":"application/json"}, csrf()), body: JSON.stringify({title, tags, body})}).then(r=>r.json())
  };
  function csrf(){
    const tok = sessionStorage.getItem("csrf");
    return tok ? {"X-CSRF-Token": tok} : {};
  }
  async // ensureCSRF provided via window.__askchip.ensureCSRF(){
    if (!sessionStorage.getItem("csrf")){
      const r = await fetch("/api/v1/auth/csrf", {credentials:"include"}).then(r=>r.json());
      if (r.ok && r.csrf) sessionStorage.setItem("csrf", r.csrf);
    }
  }
  async function refresh(){
    const q = Q("kbQuery").value, tag = Q("kbTag").value;
    const r = await api.list(q, tag);
    const list = Q("kbList"); list.innerHTML="";
    if (!r.ok) { list.textContent = "Error loading docs."; return; }
    if (!r.items.length){ list.textContent = "No documents."; return; }
    const tbl = document.createElement("table"); tbl.style.width="100%"; tbl.style.borderCollapse="collapse";
    tbl.innerHTML = `<thead><tr><th style="text-align:left">Title</th><th>Tags</th><th>Size</th><th>Chunks</th><th>Actions</th></tr></thead>`;
    const tb = document.createElement("tbody");
    r.items.forEach(it => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.title}</td><td>${it.tags||""}</td><td style="text-align:center">${it.size||""}</td><td style="text-align:center">${it.chunks||0}</td><td style="text-align:right"><button data-id="${it.id}" class="btn preview">Preview</button> <button data-id="${it.id}" class="btn danger delete">Delete</button></td>`;
      tb.appendChild(tr);
    });
    tbl.appendChild(tb); list.appendChild(tbl);
    list.querySelectorAll("button.preview").forEach(b => b.addEventListener("click", async () => {
      const id = b.getAttribute("data-id");
      const r = await api.get(id);
      if (!r.ok) return alert("Failed to load doc");
      Q("kbPrevTitle").textContent = `${r.doc.title} — ${r.doc.tags||""}`;
      Q("kbPrevBody").textContent = r.doc.body || "";
      const ol = Q("kbPrevChunks"); ol.innerHTML="";
      (r.doc.chunks||[]).forEach(c => {
        const li = document.createElement("li"); li.textContent = c.content; ol.appendChild(li);
      });
      Q("kbPreviewDlg").showModal();
    }));
    list.querySelectorAll("button.delete").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Delete this document?")) return;
      const id = b.getAttribute("data-id");
      await ensureCSRF();
      const r = await api.del(id);
      if (!r.ok) return alert("Delete failed");
      refresh();
    }));
  }
  Q("kbRefresh").addEventListener("click", refresh);
  Q("kbAdd").addEventListener("click", ()=> Q("kbAddDlg").showModal());
  Q("kbSave").addEventListener("click", async ()=> {
    const title = Q("kbTitle").value.trim();
    const tags  = Q("kbTags").value.trim();
    const body  = Q("kbBody").value.trim();
    if (!body) return alert("Body required");
    await ensureCSRF();
    const r = await api.seed(title, tags, body);
    if (!r.ok) return alert("Save failed");
    Q("kbAddDlg").close(); Q("kbBody").value=""; Q("kbTitle").value=""; Q("kbTags").value="";
    refresh();
  });
  // initial load
  refresh();
})();


/* Runtime status */
(function runtime(){
  const btn = document.getElementById("rtRefresh");
  const raw = document.getElementById("rtRaw");
  const table = document.querySelector("#rtTable tbody");
  const status = document.getElementById("rtStatus");
  function fmtProv(p){ if(!p) return "unknown"; if(!p.ok) return `ERROR: ${p.error}`; return p.name; }
  async function load(){
    if(!raw || !table || !status) return;
    status.textContent = "Loading…";
    try {
      const r = await fetch(ASKCHIP.api.runtime, { credentials: "include" });
      const j = await r.json();
      if(!j.ok) throw new Error("not ok");
      const R = j.runtime || {};
      raw.textContent = JSON.stringify(R, null, 2);
      table.innerHTML = "";
      const rows = [
        ["Env", (R.env && (R.env.APP_ENV || R.env.ENV)) || ""],
        ["Commit", R.commit || "(none)"],
        ["LLM", fmtProv(R.providers && R.providers.llm)],
        ["TTS", fmtProv(R.providers && R.providers.tts)],
        ["STT", fmtProv(R.providers && R.providers.stt)],
        ["OPENAI_API_KEY", R.keys && R.keys.OPENAI_API_KEY ? "present" : "missing"],
        ["ELEVENLABS_API_KEY", R.keys && R.keys.ELEVENLABS_API_KEY ? "present" : "missing"],
        ["SMTP ready", R.smtp_ready ? "yes" : "no"],
        ["Python", R.versions && R.versions.python || ""],
        ["openai", R.versions && R.versions.openai || ""],
        ["elevenlabs", R.versions && R.versions.elevenlabs || ""],
      ];
      rows.forEach(([k,v]) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${k}</td><td>${v}</td>`;
        table.appendChild(tr);
      });
      status.textContent = "OK";
    } catch(e){
      status.textContent = String(e && e.message || e);
    }
  }
  if(btn) btn.addEventListener("click", load);
  const tabBtn = document.getElementById("t-runtime");
  if(tabBtn) tabBtn.addEventListener("click", load);
  // initial eager fetch
  if(document.querySelector("#tab-runtime")?.getAttribute("aria-hidden")==="false"){ load(); }
})();


// PH14: populate Audio/VAD controls and save handler
document.addEventListener("DOMContentLoaded", async ()=>{
  try {
    const url = window.ASKCHIP?.api?.config_get || "/api/v1/admin/config";
    const r = await fetch(url, {credentials:"include"});
    if (!r.ok) return;
    const j = await r.json(); const cfg = j.config || j;
    const setCB=(id,v)=>{ const el=document.getElementById(id); if(el) el.checked=!!v; };
    const setNum=(id,v)=>{ const el=document.getElementById(id); if(el && v!==undefined) el.value=String(v); };
    setCB('cfg-audio_worklet_enabled', cfg.audio_worklet_enabled);
    setNum('cfg-vad_attack_ms', cfg.vad_attack_ms);
    setNum('cfg-vad_release_ms', cfg.vad_release_ms);
    setNum('cfg-vad_dbfs_threshold', cfg.vad_dbfs_threshold);
  } catch{}
});
async function cfgAudioSave(){
  const status = document.getElementById("cfgStatus") || { textContent: (t)=>{} };
  try{
    const updates = {
      audio_worklet_enabled: !!document.getElementById('cfg-audio_worklet_enabled')?.checked,
      vad_attack_ms: Number(document.getElementById('cfg-vad_attack_ms')?.value || 12),
      vad_release_ms: Number(document.getElementById('cfg-vad_release_ms')?.value || 240),
      vad_dbfs_threshold: Number(document.getElementById('cfg-vad_dbfs_threshold')?.value || -42)
    };
    const r = await fetch(window.ASKCHIP?.api?.config_set || "/api/v1/admin/config/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ updates })
    });
    status.textContent = r.ok ? "Audio/VAD settings saved." : ("Audio/VAD save failed: " + r.status);
  } catch(e){ status.textContent = "Audio/VAD save failed: " + (e?.message || e); }
}
document.addEventListener("DOMContentLoaded", ()=>{
  document.getElementById("cfgAudioSave")?.addEventListener("click", cfgAudioSave);
});
