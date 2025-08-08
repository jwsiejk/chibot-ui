document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById('basicLoginForm');
  const loginStatus = document.getElementById('loginStatus');
  const startBtn = document.getElementById('startBtn');
  const profileModal = document.getElementById('profileModal');
  const loginContainer = document.getElementById('loginContainer');
  const recordBtn = document.getElementById("recordBtn");

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

  startBtn.addEventListener("click", () => {
    const userName = localStorage.getItem("profileName") || "there";
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
        if (data.reply) {
          const statusDiv = document.getElementById("status");
          statusDiv.textContent = data.reply;
        }

        if (data.audio) {
          const greetingAudio = new Audio(data.audio);
          greetingAudio.play().catch(err =>
            console.warn("🔇 Greeting audio failed to play:", err)
          );
          // Placeholder: playVisemeAudio(data.audio, [], ...)
        }
      })
      .catch(() => {
        const statusDiv = document.getElementById("status");
        statusDiv.textContent = "👋 Hey there! I'm Chip.";
      });

    startBtn.style.display = "none";
  });
});
