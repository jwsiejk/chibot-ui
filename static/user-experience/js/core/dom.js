// core/dom.js — DOM helpers and layout utilities (r5 hotfix)
export const $ = (id) => document.getElementById(id);

export const show = (el, display) => {
  if (!el) return;
  // Critical: remove the 'hidden' class so CSS `display:none !important` doesn't win
  try { el.classList.remove("hidden"); } catch {}
  // Defensive: clear 'inert' if present so clicks are accepted
  try { el.removeAttribute("inert"); } catch {}
  if (display) el.style.display = display;
  else el.style.removeProperty("display");
};

export const hide = (el) => {
  if (!el) return;
  try { el.classList.add("hidden"); } catch {}
  el.style.display = "none";
};

export function setToolbarHeightVar(extra = 16) {
  const el = document.getElementById('askChipToolbar');
  if (!el) return;
  const h = Math.ceil(el.getBoundingClientRect().height) || 0;
  const finalPx = Math.max(h + extra, 64);
  document.documentElement.style.setProperty('--toolbar-h', finalPx + 'px');
}

export function _getQueryParam(key) {
  try { return new URL(window.location.href).searchParams.get(key); }
  catch { return null; }
}
