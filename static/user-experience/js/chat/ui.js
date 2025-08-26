// chat/ui.js — chat UI helpers, menu, suggestions
import { $ } from "../core/dom.js";

const chatLog     = () => $("chatLog");
const chatPanel   = () => $("chatPanel");
const chatInput   = () => $("chatInput");
const chatMenuBtn = () => $("chatMenuBtn");
const chatMenu    = () => $("chatMenu");

export function appendMessage(role, text, lane) {
  const log = chatLog(); if (!log) return null;
  const el = document.createElement("div");
  el.className = "msg " + role; // "user" | "assistant"
  const icon = lane ? (lane === "text" ? "💬 " : "🔊 ") : (role === "user" ? "🧑 " : "");
  el.textContent = icon + (text || "");
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

export function appendActions(actions) {
  if (!actions || !actions.length) return;
  const log = chatLog(); if (!log) return;
  const wrap = document.createElement("div");
  wrap.className = "action-row";
  for (let i = 0; i < actions.length; i++) {
    const a = actions[i];
    if (!a || !a.type) continue;
    const btn = document.createElement("button");
    btn.className = "action";
    btn.textContent = a.title || (a.type === "download" ? "Download" : "Open");
    if (a.type === "download") {
      btn.addEventListener("click", () => triggerDownload(a.url, a.filename));
    } else if (a.type === "open_url") {
      btn.addEventListener("click", () => window.open(a.url, "_blank", "noopener"));
    } else if (a.type === "show_toast") {
      btn.addEventListener("click", () => alert(a.message || "Done"));
    }
    wrap.appendChild(btn);
  }
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

function triggerDownload(url, filename) {
  if (!url) return;
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function playAudioFromBase64(b64, onended) {
  if (!b64) { if (onended) onended(); return null; }
  const audio = new Audio("data:audio/mpeg;base64," + b64);
  if (onended) audio.addEventListener("ended", onended, { once: true });
  audio.play().catch(console.error);
  return audio;
}

// Suggestions (chips) under assistant replies
export function _chipRenderSuggestions(suggestions, onPick) {
  if (!Array.isArray(suggestions) || !suggestions.length) return;
  const log = chatLog(); if (!log) return;
  const wrap = document.createElement("div");
  wrap.className = "suggestion-row";
  suggestions.forEach((s) => {
    const b = document.createElement("button");
    b.className = "suggestion";
    b.textContent = s;
    b.addEventListener("click", () => { if (typeof onPick === "function") onPick(s); });
    wrap.appendChild(b);
  });
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

// Chat lane dropdown + panel
export function updateChatButtonLabel(chatLane) {
  const btn = chatMenuBtn(); if (!btn) return;
  btn.textContent = (chatLane === "text") ? "💬 Chat (Text) ▾" : "💬 Chat (Live) ▾";
}

export function toggleChatMenu(forceOpen) {
  const menu = chatMenu(); if (!menu) return;
  if (typeof forceOpen === "boolean") {
    menu.style.display = forceOpen ? "block" : "none";
    return;
  }
  menu.style.display = (menu.style.display === "block") ? "none" : "block";
}

export function wireChatMenu(chatLaneGet, chatLaneSet, onPickLane) {
  const btn = chatMenuBtn();
  const menu = chatMenu();

  btn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const panel = chatPanel(); if (panel) { panel.hidden = false; chatInput()?.focus(); }
    toggleChatMenu();
  });

  document.addEventListener("click", (e) => {
    if (!menu) return;
    if (menu.style.display === "block" && !menu.contains(e.target) && e.target !== btn) {
      toggleChatMenu(false);
    }
  });

  menu?.addEventListener("click", (e) => {
    const t = e.target;
    if (!t || !t.getAttribute) return;
    const lane = t.getAttribute("data-lane");
    if (!lane) return;
    chatLaneSet(lane === "text" ? "text" : "live");
    updateChatButtonLabel(chatLaneGet());
    toggleChatMenu(false);
    const panel = chatPanel(); if (panel) { panel.hidden = false; chatInput()?.focus(); }
    if (typeof onPickLane === "function") onPickLane(chatLaneGet());
  });
}
