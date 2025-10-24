(() => {
  const ROOT_SCOPE = typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : {};

  function createMessage() {
    const container = document.createElement("div");
    container.classList.add("config-panel__message");
    container.style.padding = "16px";
    container.style.borderRadius = "8px";
    container.style.background = "#f5f5f5";
    container.style.color = "#1f2933";
    container.style.fontSize = "14px";
    container.style.lineHeight = "1.6";
    container.style.border = "1px solid rgba(31, 41, 51, 0.12)";

    const heading = document.createElement("h2");
    heading.textContent = "Authentication";
    heading.style.margin = "0 0 8px";
    heading.style.fontSize = "16px";

    const body = document.createElement("p");
    body.textContent = "Token-based authentication is disabled. Admin and WebSocket endpoints are available without bearer tokens.";
    body.style.margin = "0";

    container.appendChild(heading);
    container.appendChild(body);
    return container;
  }

  function init(root) {
    if (!root || !(root instanceof HTMLElement)) {
      throw new TypeError("AdminConfigPanel.init requires a root element");
    }

    root.innerHTML = "";
    root.appendChild(createMessage());
  }

  ROOT_SCOPE.AdminConfigPanel = Object.assign(ROOT_SCOPE.AdminConfigPanel || {}, { init });
})();
