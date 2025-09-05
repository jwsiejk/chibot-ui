// static/js/admin.js
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* Tabs */
(function tabs(){
  const ts = $$(".tab");
  const panels = [$("#tab-logs"), $("#tab-config"), $("#tab-layout"), $("#tab-users")];
  ts.forEach(t => t.addEventListener("click", () => {
    ts.forEach(x => x.setAttribute("aria-selected", "false"));
    t.setAttribute("aria-selected", "true");
    panels.forEach(p => p.setAttribute("aria-hidden", "true"));
    const target = document.getElementById(t.getAttribute("aria-controls"));
    if (target) target.setAttribute("aria-hidden", "false");
  }));
})();

/* Live Logs (SSE) with filter + clear */
(function sseLogs(){
  const url = window.ASKCHIP.api.logs;
  const el = $("#adminLog");
  try {
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      const line = ev.data || "";
      const f = ($("#logFilter").value || "").toLowerCase();
      if (!f || line.toLowerCase().includes(f)) {
        el.textContent += (line + "\n");
        el.scrollTop = el.scrollHeight;
      }
    };
    es.onerror = () => { el.textContent += "\n[disconnected]\n"; };
    $("#logClear").addEventListener("click", () => { el.textContent = ""; });
  } catch (e) {
    el.textContent = String(e);
  }
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
async function lyLoad(variant){
  const url = `${window.ASKCHIP.api.layout_get}?variant=${encodeURIComponent(variant)}&breakpoint=desktop`;
  const status = $("#lyStatus");
  try {
    const r = await fetch(url, {credentials:"include"});
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    const j = await r.json();
    const L = j.layout || {};
    $("#stageSide").value = L.stage_side || "left";
    $("#showStrip").checked = !!L.show_instruction_strip;
    $("#showDots").checked  = !!L.show_state_dots;
    status.textContent = `Loaded ${variant} (v${j.version || 1})`;
  } catch (e) {
    status.textContent = String(e.message || e);
  }
}
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
    const r = await fetch(window.ASKCHIP.api.layout_set, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "include",
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`POST /layouts → ${r.status}`);
    const j = await r.json();
    status.textContent = `Draft saved (v${j.version})`;
  } catch (e) {
    status.textContent = String(e.message || e);
  }
}
async function lyPublish(){
  // publish = save directly to 'published'
  const status = $("#lyStatus");
  const body = {
    variant: "published",
    breakpoint: "desktop",
    layout: {
      stage_side: $("#stageSide").value,
      show_instruction_strip: !!$("#showStrip").checked,
      show_state_dots: !!$("#showDots").checked
    }
  };
  try {
    const r = await fetch(window.ASKCHIP.api.layout_set, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "include",
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`POST /layouts → ${r.status}`);
    const j = await r.json();
    status.textContent = `Published (v${j.version}) — all users will see it on next load.`;
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
