const UI = (() => {
  const loginView = document.getElementById("loginView");
  const profileView = document.getElementById("profileView");
  const chatView = document.getElementById("chatView");
  const appStatus = document.getElementById("appStatus");
  const chatLog = document.getElementById("chatLog");
  const userMenu = document.getElementById("userMenu");
  const userEmail = document.getElementById("userEmail");
  const chipImage = document.getElementById("chipImage");

  function show(section) {
    loginView.hidden = section !== "login";
    profileView.hidden = section !== "profile";
    chatView.hidden = section !== "chat";
  }
  function setStatus(text) { appStatus.textContent = text || "Ready"; }
  function setUser(email) {
    if (email) { userMenu.hidden = false; userEmail.textContent = email; }
    else { userMenu.hidden = true; userEmail.textContent = ""; }
  }
  function appendBubble(role, text) {
    const div = document.createElement("div");
    div.className = "bubble " + (role === "user" ? "from-user" : "from-assistant");
    div.textContent = text;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }
  function clearChat(){ chatLog.innerHTML = ""; }

  (function initImage(){
    const tryPaths = ["/static/chip/img/chip.png","/static/chip/img/chip.jpg","/static/chip/img/chip.jpeg","/static/chip/img/chip.svg"];
    let idx = 0;
    function attempt() {
      if (idx >= tryPaths.length) return;
      const src = tryPaths[idx++];
      const img = new Image();
      img.onload = () => { chipImage.src = src; };
      img.onerror = attempt;
      img.src = src;
    }
    attempt();
  })();

  return { show, setStatus, setUser, appendBubble, clearChat };
})();
