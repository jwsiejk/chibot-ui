// core/dom.js — DOM helpers and layout utilities
export const $ = (id) => document.getElementById(id);

export const show = (el, display) => {
  if (!el) return;
  if (display) el.style.display = display;
  else el.style.removeProperty("display");
};

export const hide = (el) => { if (el) el.style.display = "none"; };

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
