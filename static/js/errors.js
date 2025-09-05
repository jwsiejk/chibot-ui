let banner;
function ensureBanner(){
  if (banner) return banner;
  banner = document.createElement("div");
  banner.id = "error-banner";
  banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:#b00020;color:#fff;padding:8px 12px;z-index:9999;display:none;font:14px system-ui;";
  document.body.appendChild(banner);
  return banner;
}

export function showError(route, status, text=""){
  const b = ensureBanner();
  b.textContent = `Error: ${route} → HTTP ${status}${text?` — ${text}`:""}`;
  b.style.display = "block";
}
export function hideError(){
  if (!banner) return;
  banner.style.display = "none";
}