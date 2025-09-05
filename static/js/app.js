// static/js/app.js — v2: robust wiring + CSRF + clear errors

/* ------- tiny helpers ------- */
const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function log(...args){ try{ console.log("[AskChip]", ...args); }catch{} }
function showErr(msg){ const b=$("#errorBanner"); if(!b) return; b.textContent=String(msg||"Error"); b.classList.add("show"); }
function clearErr(){ const b=$("#errorBanner"); if(!b) return; b.classList.remove("show"); b.textContent=""; }

/* ------- CSRF (works with common cookie names) ------- */
function getCookie(name){
  const m = document.cookie.match(new RegExp("(^|; )" + name.replace(/[-.$?*|{}()[]\\/+^]/g, "\\$&") + "=([^;]*)"));
  return m ? decodeURIComponent(m[2]) : null;
}
function csrfHeader(){
  // try common names your middleware might use
  const v = getCookie("csrf_token") || getCookie("csrftoken") || getCookie("XSRF-TOKEN") || getCookie("csrf");
  return v ? { "X-CSRFToken": v } : {};
}

/* ------- state dots ------- */
const dots = (() => {
  const set = (phase) => {
    $$(".state-dots .dot").forEach(d => d.classList.remove("active"));
    const dot = $(`.dot[data-state="${phase}"]`);
    if (dot) dot.classList.add("active");
    const s = $("#statusText"); if (s) s.textContent = phase[0].toUpperCase()+phase.slice(1);
  };
  return { set };
})();

/* ------- websocket ------- */
let ws = null;
let hb = null;

function wsConnect() {
  const url = window.ASKCHIP?.api?.ws;
  if (!url) { showErr("WS url missing"); return; }

  log("WS connect →", url);
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.addEventListener("open", () => {
    clearErr();
    $("#btnEnd") && ($("#btnEnd").disabled = false);
    // heartbeat ping (app-level)
    const interval = (window.ASKCHIP?.config?.ws_ping_interval_ms) || 25000;
    hb = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    }, interval);
    dots.set("ready");
  });

  ws.addEventListener("message", (e) => {
    let m = null;
    try { m = JSON.parse(e.data); } catch {}
    if (!m) return;
    if (m.type === "state") {
      if (m.phase === "assistant_speaking") dots.set("responding");
      else if (m.phase === "assistant_end" || m.phase === "ready") dots.set("ready");
    } else if (m.type === "text") {
      addMsg("assistant", m.content || "");
    } else if (m.type === "error") {
      showErr(m.message || "Server error");
    } else if (m.type === "pong") {
      // heartbeat OK
    }
  });

  ws.addEventListener("close", () => {
    if (hb) clearInterval(hb);
    $("#btnEnd") && ($("#btnEnd").disabled = true);
    log("WS closed");
  });

  ws.addEventListener("error", () => {
    showErr("WebSocket error");
  });
}

/* ------- greet + chat ------- */
async function greet() {
  const url = window.ASKCHIP?.api?.greet;
  if (!url) { showErr("greet url missing"); return; }
  log("GET", url);
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`/api/v1/greet → ${r.status}`);
    const j = await r.json().catch(()=>({}));
    if (j?.text) addMsg("assistant", j.text);
  } catch (e) {
    showErr(e.message || String(e));
  }
}

async function sendChat(text) {
  const url = window.ASKCHIP?.api?.chat;
  if (!url) { showErr("chat url missing"); return; }
  const body = JSON.stringify({ text: String(text||"") });
  const headers = Object.assign({ "Content-Type": "application/json" }, csrfHeader());

  log("POST", url, body);
  try {
    const r = await fetch(url, { method: "POST", headers, credentials: "include", body });
    if (!r.ok) throw new Error(`/api/v1/chat → ${r.status}`);
    // Assistant reply will stream over WS; we don't need the response body here.
  } catch (e) {
    showErr(e.message || String(e));
  }
}

/* ------- chat UI ------- */
function addMsg(role, text){
  const body = $("#chatBody");
  if (!body) return;
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.textContent = text;
  body.appendChild(d);
  body.scrollTop = body.scrollHeight;
}

/* ------- wire buttons ------- */
function wireUI(){
  const start = $("#btnStart");
  const end   = $("#btnEnd");
  const mute  = $("#btnMute");
  const chatT = $("#btnChat");
  const send  = $("#chatSend");
  const input = $("#chatInput");

  if (start) {
    start.addEventListener("click", async () => {
      log("Start clicked");
      start.disabled = true;
      wsConnect();
      await greet();
      start.disabled = false;
    });
  }

  if (end) {
    end.addEventListener("click", () => {
      log("End clicked");
      try { ws && ws.close(); } catch {}
    });
  }

  if (mute) {
    mute.addEventListener("click", (ev) => {
      const pressed = ev.currentTarget.getAttribute("aria-pressed") === "true";
      ev.currentTarget.setAttribute("aria-pressed", (!pressed).toString());
      ev.currentTarget.textContent = (!pressed) ? "Audio: Off" : "Audio: On";
      log("Mute toggled →", !pressed ? "off" : "on");
    });
  }

  if (chatT) {
    chatT.addEventListener("click", (ev) => {
      const pressed = ev.currentTarget.getAttribute("aria-pressed") === "true";
      ev.currentTarget.setAttribute("aria-pressed", (!pressed).toString());
      $("#chatPane").style.display = pressed ? "none" : "flex";
      log("Chat pane", pressed ? "hidden" : "shown");
    });
  }

  if (send && input) {
    send.addEventListener("click", async () => {
      const text = input.value.trim();
      if (!text) return;
      addMsg("user", text);
      input.value = "";
      await sendChat(text);
    });
    // Enter to send
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send.click();
      }
    });
  }

  // Initial state
  dots.set("ready");
}

/* ------- boot ------- */
window.addEventListener("DOMContentLoaded", () => {
  try {
    // Set initial mouth sprite (matches your 2D pack naming)
    const base = window.ASKCHIP?.assets?.visemeBase || "/static/visemes/chip-2d-pack/";
    const m = $("#chipMouth");
    if (m) { m.src = base + "mouth_neutral.png"; m.style.display = "block"; }
    wireUI();
    clearErr();
  } catch (e) {
    showErr(e.message || String(e));
  }
});
