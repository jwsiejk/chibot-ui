document.addEventListener("DOMContentLoaded", () => {
    console.log("🚀 main.js loaded (consolidation pass)");

    const loginModal = document.getElementById("loginModal");
    const profileModal = document.getElementById("profileModal");
    const startButton = document.getElementById("startButton");
    const saveProfileBtn = document.getElementById("saveProfileBtn");
    const loginHint = document.getElementById("loginHint");
    const profileHint = document.getElementById("profileHint");

    // Guidance text
    if (loginHint) {
        loginHint.textContent = "Please sign in with your Trace3 or Pure Storage email address.";
    }
    if (profileHint) {
        profileHint.textContent = "Please fill out your profile to continue.";
    }

    // Disable Start initially
    if (startButton) {
        startButton.disabled = true;
    }

    // Check auth status
    fetch("/auth/status")
        .then(r => r.json())
        .then(data => {
            console.log("Auth status:", data);
            if (!data.authenticated) {
                // Not logged in → show login modal
                if (loginModal) loginModal.style.display = "block";
            } else if (!data.profileComplete) {
                // Logged in but no profile
                if (profileModal) profileModal.style.display = "block";
            } else {
                // Logged in & profile complete
                if (startButton) startButton.disabled = false;
            }
        })
        .catch(err => console.error("Error checking auth:", err));

    // Login form
    const loginForm = document.getElementById("loginForm");
    if (loginForm) {
        loginForm.addEventListener("submit", e => {
            e.preventDefault();
            const formData = new FormData(loginForm);
            fetch("/login", {
                method: "POST",
                body: formData
            })
                .then(r => r.json())
                .then(data => {
                    console.log("Login response:", data);
                    if (data.success) {
                        if (loginModal) loginModal.style.display = "none";
                        if (!data.profileComplete) {
                            if (profileModal) profileModal.style.display = "block";
                        } else {
                            if (startButton) startButton.disabled = false;
                        }
                    } else {
                        alert("Login failed");
                    }
                })
                .catch(err => console.error("Login error:", err));
        });
    }

    // Save profile
    if (saveProfileBtn) {
        saveProfileBtn.addEventListener("click", () => {
            const profileForm = document.getElementById("profileForm");
            if (!profileForm) return;
            const formData = new FormData(profileForm);
            fetch("/profile", {
                method: "POST",
                body: formData
            })
                .then(r => r.json())
                .then(data => {
                    console.log("Profile save response:", data);
                    if (data.success) {
                        if (profileModal) profileModal.style.display = "none";
                        if (startButton) startButton.disabled = false;
                    } else {
                        alert("Error saving profile");
                    }
                })
                .catch(err => console.error("Profile save error:", err));
        });
    }
});
