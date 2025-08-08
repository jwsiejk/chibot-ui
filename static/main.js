
document.addEventListener("DOMContentLoaded", function () {
console.log("🧪 Loaded index_3d.html vDEBUG-9");

    function autoStartListening() {
      setTimeout(() => {
        const btn = document.getElementById("recordBtn");
        if (btn) btn.click();
      }, 500);
    }

    document.addEventListener("DOMContentLoaded", () => {
<!-- PATCH: Forced Login & Profile Gating | 2025-08-07 -->

  // Enforce login on load
  // 
  // 
  // 

  loginContainer.style.display = "block";
  startBtn.disabled = true;

  // Save profile with gating message and unlock Start button
  window.saveProfile = function() {
    const name = document.getElementById("profileName").value;
    const title = document.getElementById("profileTitle").value;
    if (!name || !title) {
      alert("Please complete all fields.");
      return;
    }

    localStorage.setItem("profileName", name);
    localStorage.setItem("profileTitle", title);

    console.log("📤 Sending profile to /profile:", { name, role: title });
  fetch('/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        role: title
      })
    }).then(res => {
      if (res.ok) {
        console.log("✅ Profile saved to DB");
        startBtn.disabled = false;
        profileModal.style.display = "none";
      } else {
        alert("❌ Failed to save profile. Try again.");
      }
    }).catch(err => {
      console.error("❌ Network error while saving profile:", err);
      alert("❌ Network error while saving profile.");
    });
  };
        
      const profileBtn = document.getElementById("profileBtn");
      if (profileBtn) profileBtn.addEventListener("click", () => {
        const modal = document.getElementById("profileModal");
        modal.style.display = "block";
        document.getElementById("profileName").value = localStorage.getItem("profileName") || "";
        document.getElementById("profileTitle").value = localStorage.getItem("profileTitle") || "";
      });

      
      window.saveProfile = function() {
        const name = document.getElementById("profileName").value;
        const title = document.getElementById("profileTitle").value;

        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);

        console.log("📤 Sending profile to /profile:", { name, role: title });
  fetch('/profile', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: name,
            role: title
          })
        }).then(res => {
          if (res.ok) {
            console.log("✅ Profile saved to DB");
          } else {
            console.error("❌ Failed to save profile to DB");
          }
          closeProfile();
        }).catch(err => {
          console.error("❌ Network error while saving profile:", err);
          closeProfile();
        });
      };


      window.closeProfile = function() {
        const modal = document.getElementById("profileModal");
        modal.style.display = "none";
      };


      function updateStatus(message) {
        const statusDiv = document.getElementById("status");
        statusDiv.textContent = message;
        console.log("[Chip]", message);
      }

      
      
      
      if (startBtn) startBtn.addEventListener("click", () => {
        window.silenceCounter = 0; // reset silence tracking

<!-- PATCH: Dynamic Greeting | 2025-08-07 -->

        const userName = localStorage.getItem("profileName") || "there";
        const now = new Date();
        const hours = now.getHours();
        const isMorning = hours < 12;
        const greetingPrompt = `
Chip is a friendly, dry-witted, Nebraskan AI with a subtle sense of humor.
Create a short greeting using the following:
- Starts with "Good morning" or "Good afternoon" based on current time.
- Include the user name if available (name: "${userName}").
- Ends with a natural, conversational variant of "How may I help you today?"
Make it sound like Chip, and no more than 20 words.
`;

        fetch("/greet", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: greetingPrompt })
        })
        .then(res => res.json())
        .then(data => {
            if (data.reply) updateStatus(data.reply);
            if (data.audio) {
                const greetingAudio = new Audio(data.audio);
                greetingAudio.play().catch(err => console.warn("🔇 Greeting audio failed to play:", err));
                playVisemeAudio(data.audio, [], "👋 Ask me anything about Pure Storage when you're ready (5 seconds).");
            }
        })
        .catch(() => {
            updateStatus("👋 Hey there! I'm Chip.");
        });

        startBtn.style.display = "none";
                fetch("/greet")
          .then(res => res.json())
          .then(data => {
              if (data.reply) updateStatus(data.reply);
              if (data.audio) {
                
    const greetingAudio = new Audio(data.audio);
    greetingAudio.play().catch(err => console.warn("🔇 Greeting audio failed to play:", err));
    playVisemeAudio(data.audio, [], "👋 Ask me anything about Pure Storage when you're ready (5 seconds).");
    
              }
              })
          .catch(() => {
            updateStatus("👋 Hey there! I'm Chip.");
              });
        startBtn.style.display = "none";
      });

      const exitBtn = document.getElementById("exitBtn");
      const resetBtn = document.getElementById("resetBtn");

      if (exitBtn) exitBtn.addEventListener("click", () => {
        updateStatus("👋 It was great talking with you.");
        document.getElementById("recordPrompt").innerText = "👋 Chip has left the session.";
        document.getElementById("recordPrompt").style.display = "block";
        if (recordBtn) recordBtn.style.display = "none";
        window.stopPassiveLoop = true;
      });

      if (resetBtn) resetBtn.addEventListener("click", async () => {
        updateStatus("🔄 Restarting session...");
        document.getElementById("recordPrompt").innerText = "🔁 Resetting...";
        document.getElementById("recordPrompt").style.display = "block";
        if (recordBtn) recordBtn.style.display = "none";
        window.stopPassiveLoop = false;
        await new Promise(resolve => setTimeout(resolve, 1500));
        fetch("/greet").then(res => res.json()).then(data => {
          updateStatus(data.reply || "👋 Welcome back.");
            if (data.audio) {
              
    const greetingAudio = new Audio(data.audio);
    greetingAudio.play().catch(err => console.warn("🔇 Greeting audio failed to play:", err));
    playVisemeAudio(data.audio, [], "👋 Ask me anything about Pure Storage when you're ready (5 seconds).");
    
            }
          });
      });

      recordBtn.addEventListener("click", async () => {
        if (recordBtn.disabled) return;
        if (recordBtn) recordBtn.disabled = true;
        document.getElementById("recordPrompt").classList.remove("idle");
        document.getElementById("recordPrompt").classList.add("listening");
        console.log("⛔ Blinking OFF");
        if (recordBtn) recordBtn.classList.remove("blinking");

        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const recorder = new MediaRecorder(stream);
          const chunks = [];

          recorder.ondataavailable = e => chunks.push(e.data);

          recorder.onstop = async () => {
            updateStatus("📤 Uploading audio to Chip...");
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const audioLink = document.getElementById('audioDownload');
            audioLink.href = URL.createObjectURL(blob);
            audioLink.style.display = 'block';

            const formData = new FormData();
            formData.append("audio", blob, "input.webm");
            updateStatus("🧠 Processing request...");
        document.getElementById("recordPrompt").innerText = "🤔 Chip is thinking...";

            const response = await fetch("/ask-chip", { method: "POST", body: formData });
            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            let visemes = [];
            let audioUrl = "";
            let audioChunks = [];
            let collectingAudio = false;

            const streamTimeout = setTimeout(() => {
              console.warn("⏰ Stream timeout: forcing finalization.");
              reader.cancel();
              }, 10000);

            while (true) {
              const { done, value } = await reader.read();
              if (done) {
                clearTimeout(streamTimeout);
                console.log("📦 Stream ended. Finalizing audio...");
                break;
                }

              if (value && value.length > 2) {
                if (value[0] === 73 && value[1] === 68 && value[2] === 51) {
                  console.log("🎧 Detected audio/mpeg binary start (ID3)");
                  collectingAudio = true;
                  }
                if (collectingAudio) {
                  audioChunks.push(value);
                  console.log("📥 Appended audio chunk, total:", audioChunks.length);
                  continue;
                  }
                }

              const text = decoder.decode(value);
              const lines = text.split("--frame").filter(Boolean);
              for (const line of lines) {
                const [, headers, content] = line.split(/\r?\n\r?\n/);
                if (!headers || !content) continue;

                const contentType = headers.match(/Content-Type: (.+)/)?.[1];
                if (contentType === "application/json") {
                  const data = JSON.parse(content);
                  if (data.viseme_timestamps) visemes = data.viseme_timestamps;
                  if (data.audio) audioUrl = data.audio;
                  if (data.transcript) {

                    if (!data.transcript || data.transcript.trim() === "") {
                      window.silenceCounter = (window.silenceCounter || 0) + 1;
                      if (window.silenceCounter === 1) {
                        updateStatus("🤔 I didn’t hear anything. Is there something I can help you with?");
                      } else {
                        updateStatus("👋 I’m not picking anything up, so I’m going to jump off. Press Start if you want to talk.");
                        document.getElementById("recordPrompt").innerText = "👋 Chip is offline.";
                        window.stopPassiveLoop = true;
                      }
                      return;
                    } else {
                      window.silenceCounter = 0;
                    }


                    if (!data.transcript || data.transcript.trim() === "") {
                      window.silenceCounter = (window.silenceCounter || 0) + 1;
                      if (window.silenceCounter === 1) {
                        updateStatus("🤔 I didn’t hear anything. Is there something I can help you with?");
                      } else {
                        updateStatus("👋 I’m not picking anything up, so I’m going to jump off. Press Start if you want to talk.");
                        document.getElementById("recordPrompt").innerText = "👋 Chip is offline.";
                        window.stopPassiveLoop = true;
                      }
                      return;
                    } else {
                      window.silenceCounter = 0;
                    }

                    const exitPhrase = data.transcript.trim().toLowerCase();
                    if (exitPhrase.includes("that's all for now") || exitPhrase.includes("have a nice day") || exitPhrase.includes("catch you later") || exitPhrase.includes("i'm signing off") || exitPhrase.includes("exit")) {
                      updateStatus("👋 It was great talking with you.");
                      document.getElementById("recordPrompt").innerText = "👋 Chip has left the session.";
                      document.getElementById("recordPrompt").classList.add("ready");
                      document.getElementById("recordPrompt").style.display = "block";
                      return; // stop passive loop
                      }
                    updateStatus("🗣️ You said: " + data.transcript);
                    const caption = document.getElementById("caption");
                    caption.textContent = data.transcript;
                    caption.style.visibility = "visible";
                    caption.style.display = "block";
                    setTimeout(() => { caption.style.display = "none"; }, 4000);
                    }
                  if (data.response) console.log("🤖 Chip replied:", data.response);
                  }
                }
              }

            if (audioChunks.length) {
              const fullAudioBlob = new Blob(audioChunks, { type: 'audio/mpeg' });
              audioUrl = URL.createObjectURL(fullAudioBlob);
              console.log("🔗 Final audioUrl created from chunks:", audioUrl);
              }

            if (audioUrl) {
              playVisemeAudio(audioUrl, visemes);
              } else {
              updateStatus("⚠️ Failed to get audio or visemes.");
              recordBtn.style.display = "block";
              }
            };

          recorder.start();
          setTimeout(() => recorder.stop(), 5000);
        } catch (err) {
          updateStatus("❌ Microphone access failed.");
          console.error(err);
        }
      });
    });

document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById('basicLoginForm');
  const loginStatus = document.getElementById('loginStatus');
  const startBtn = document.getElementById('startBtn');
  const profileModal = document.getElementById('profileModal');

  startBtn.disabled = true;

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const loginName = document.getElementById('loginName').value;

    try {
      const res = await fetch('/login-basic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login: loginName })
      });

      if (res.ok) {
        const data = await res.json();
        loginStatus.textContent = '✅ Login successful';
        document.getElementById("loginContainer").style.display = "none";
        startBtn.disabled = false;

        if (data.first_time) {
          profileModal.style.display = "block";
localStorage.setItem("chip_login", loginName);
          document.getElementById("profileName").value = loginName;
document.getElementById("profileTitle").value = "";
        } else {
          // Populate profile fields
          document.getElementById("profileName").value = data.name;
          document.getElementById("profileTitle").value = data.title;
        }
      } else {
        loginStatus.textContent = '❌ Login failed';
      }
    } catch (err) {
      loginStatus.textContent = '❌ Error during login';
      console.error(err);
    }
  });
});

window.saveProfile = function () {
  let name = document.getElementById("profileName").value.trim();
    if (!name) name = localStorage.getItem("chip_login") || "";
  const title = document.getElementById("profileTitle").value.trim();
  
  

  if (!name || !title) {
    alert("Please complete all fields.");
    return;
  }

  localStorage.setItem("profileName", name);
  localStorage.setItem("profileTitle", title);

  console.log("📤 Sending profile to /profile:", { name, role: title });
  fetch('/profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, role: title })
  })
  .then(res => {
    if (res.ok) {
      console.log("✅ Profile saved to DB");
      startBtn.disabled = false;
      profileModal.style.display = "none";
    } else {
      alert("❌ Failed to save profile. Try again.");
    }
  })
  .catch(err => {
    console.error("❌ Network error while saving profile:", err);
    alert("❌ Network error while saving profile.");
  });
};

document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById('basicLoginForm');
  const loginStatus = document.getElementById('loginStatus');
  const startBtn = document.getElementById('startBtn');
  const profileModal = document.getElementById('profileModal');
  

  startBtn.disabled = true;

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const loginName = document.getElementById('loginName').value;

    try {
      const res = await fetch('/login-basic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login: loginName })
      });

      if (res.ok) {
        const data = await res.json();
        loginStatus.textContent = '✅ Login successful';
        loginContainer.style.display = "none";

        // Show profile modal if missing info
        if (data.first_time || !data.name || !data.title) {
          profileModal.style.display = "block";
          localStorage.setItem("chip_login", loginName);
          document.getElementById("profileName").value = loginName;
          document.getElementById("profileTitle").value = "";
          startBtn.disabled = true;
        } else {
          localStorage.setItem("profileName", data.name);
          localStorage.setItem("profileTitle", data.title);
          startBtn.disabled = false;
        }
      } else {
        loginStatus.textContent = '❌ Login failed';
      }
    } catch (err) {
      loginStatus.textContent = '❌ Error during login';
      console.error(err);
    }
  });

  window.saveProfile = function () {
    let name = document.getElementById("profileName").value.trim();
    if (!name) name = localStorage.getItem("chip_login") || "";
    const title = document.getElementById("profileTitle").value.trim();

    if (!name || !title) {
      alert("Please complete all fields.");
      return;
    }

    localStorage.setItem("profileName", name);
    localStorage.setItem("profileTitle", title);

    console.log("📤 Sending profile to /profile:", { name, role: title });
  fetch('/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, role: title })
    })
    .then(res => {
      if (res.ok) {
        console.log("✅ Profile saved to DB");
        document.getElementById("startBtn").disabled = false;
        document.getElementById("profileModal").style.display = "none";
      } else {
        alert("❌ Failed to save profile. Try again.");
      }
    })
    .catch(err => {
      console.error("❌ Network error while saving profile:", err);
      alert("❌ Network error while saving profile.");
    });
  };
});

document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById('basicLoginForm');
  const loginStatus = document.getElementById('loginStatus');
  const startBtn = document.getElementById('startBtn');
  const profileModal = document.getElementById('profileModal');
  

  // Restore login message
  const loginMsg = document.createElement("div");
  loginMsg.innerText = "Sign on with your Trace3 or Pure Storage email address to continue";
  loginMsg.style.color = "#fff";
  loginMsg.style.marginTop = "8px";
  loginContainer.appendChild(loginMsg);

  startBtn.disabled = true;

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const loginName = document.getElementById('loginName').value;

    try {
      const res = await fetch('/login-basic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login: loginName })
      });

      if (res.ok) {
        const data = await res.json();
        loginStatus.textContent = '✅ Login successful';
        loginContainer.style.display = "none";

        if (data.first_time || !data.name || !data.title) {
          profileModal.style.display = "block";
          localStorage.setItem("chip_login", loginName);
          document.getElementById("profileName").value = loginName;
          document.getElementById("profileTitle").value = "";
          startBtn.disabled = true;
        } else {
          localStorage.setItem("profileName", data.name);
          localStorage.setItem("profileTitle", data.title);
          localStorage.setItem("chip_name", data.name);
          localStorage.setItem("chip_title", data.title);
          startBtn.disabled = false;
        }
      } else {
        loginStatus.textContent = '❌ Login failed';
      }
    } catch (err) {
      loginStatus.textContent = '❌ Error during login';
      console.error(err);
    }
  });

  // Updated saveProfile to write all required keys
  window.saveProfile = function () {
    let name = document.getElementById("profileName").value.trim();
    if (!name) name = localStorage.getItem("chip_login") || "";
    const title = document.getElementById("profileTitle").value.trim();

    if (!name || !title) {
      alert("Please complete all fields.");
      return;
    }

    // Save all required keys
    localStorage.setItem("profileName", name);
    localStorage.setItem("profileTitle", title);
    localStorage.setItem("chip_name", name);
    localStorage.setItem("chip_title", title);

    console.log("📤 Sending profile to /profile:", { name, role: title });
  fetch('/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, role: title })
    })
    .then(res => {
      if (res.ok) {
        console.log("✅ Profile saved to DB");
        startBtn.disabled = false;
        profileModal.style.display = "none";
      } else {
        alert("❌ Failed to save profile. Try again.");
      }
    })
    .catch(err => {
      console.error("❌ Network error while saving profile:", err);
      alert("❌ Network error while saving profile.");
    });
  };
});
});


/* ===== PATCH: Auth gating via /auth/status (non-destructive) | 2025-08-08 ===== */
(function () {
  // Helpers (no global leaks)
  function $(sel) { return document.querySelector(sel); }
  function show(el) { if (el) el.style.display = "block"; }
  function hide(el) { if (el) el.style.display = "none"; }
  function enable(el) { if (el) el.disabled = false; }
  function disable(el) { if (el) el.disabled = true; }

  async function getStatus() {
    try {
      const res = await fetch("/auth/status", { credentials: "same-origin" });
      if (!res.ok) return { authenticated: false };
      return await res.json();
    } catch (_) {
      return { authenticated: false };
    }
  }

  async function initAuthGating() {
    const loginContainer = $("#loginContainer");
    const startBtn = $("#startBtn");
    const profileModal = $("#profileModal");

    // Default safe state
    if (startBtn) disable(startBtn);

    const st = await getStatus();

    if (!st.authenticated) {
      show(loginContainer);
      hide(profileModal);
      if (startBtn) disable(startBtn);
      return;
    }

    // Authenticated
    hide(loginContainer);
    if (st.first_time) {
      show(profileModal);
      if (startBtn) disable(startBtn);
    } else {
      hide(profileModal);
      if (startBtn) enable(startBtn);
    }
  }

  // Wire login form to /login (fallback to /login-basic) without breaking existing handlers
  document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("basicLoginForm");
    const statusEl = document.getElementById("loginStatus");
    const startBtn = document.getElementById("startBtn");
    const loginContainer = document.getElementById("loginContainer");
    const profileModal = document.getElementById("profileModal");

    // Run initial gating
    initAuthGating();

    if (form && !form.dataset.authWired) {
      form.dataset.authWired = "1";
      form.addEventListener("submit", async function (e) {
        try {
          // Let any existing listeners run first
          // We only augment by retrying /login if /login-basic fails, then re-run gating
          setTimeout(async function () {
            // If Start already enabled by existing code, we're good
            if (startBtn && !startBtn.disabled) return;

            // Try /login if previous flow didn't authorize
            const emailInput = document.getElementById("loginName");
            const email = emailInput ? emailInput.value.trim() : "";
            if (!email) return;

            let ok = false;
            try {
              const r = await fetch("/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({ email })
              });
              ok = r.ok;
            } catch (_) {}

            if (ok) {
              if (statusEl) statusEl.textContent = "✅ Login successful";
              if (loginContainer) hide(loginContainer);
              await initAuthGating();
            }
          }, 0);
        } catch (err) {
          console.warn("Augmented login handler error:", err);
        }
      });
    }

    // Replace/augment saveProfile to support /profile/save fallback and enabling Start
    window.saveProfile = async function saveProfile() {
      const nameEl = document.getElementById("profileName");
      const titleEl = document.getElementById("profileTitle");
      const name = (nameEl && nameEl.value || "").trim() || (localStorage.getItem("chip_login") || "");
      const title = (titleEl && titleEl.value || "").trim();

      if (!name || !title) {
        alert("Please complete all fields.");
        return;
      }

      // Persist locally for existing UI expectations
      try {
        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);
        localStorage.setItem("chip_name", name);
        localStorage.setItem("chip_title", title);
      } catch (_) {}

      const body = JSON.stringify({ name, title, role: title });
      const opts = { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body };

      let saved = false;
      try {
        const r1 = await fetch("/profile/save", opts);
        saved = r1.ok;
      } catch (_) {}

      if (!saved) {
        try {
          const r2 = await fetch("/profile", opts);
          saved = r2.ok;
        } catch (_) {}
      }

      if (saved) {
        if (profileModal) hide(profileModal);
        if (startBtn) enable(startBtn);
        const status = document.getElementById("status");
        if (status) status.textContent = "Profile saved. Ready.";
      } else {
        alert("❌ Failed to save profile. Try again.");
      }
    };
  });
})();
/* ===== END PATCH ===== */
