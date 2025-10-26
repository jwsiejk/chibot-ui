(() => {
  const loginModal = document.getElementById("loginModal");
  const profileModal = document.getElementById("profileModal");
  if (!loginModal || !profileModal) {
    return;
  }

  const loginForm = document.getElementById("loginForm");
  const profileForm = document.getElementById("profileForm");
  const loginEmailInput = document.getElementById("loginEmail");
  const profileEmailInput = document.getElementById("profileEmail");
  const profileNameInput = document.getElementById("profileName");
  const profileTitleInput = document.getElementById("profileTitle");
  const profileRegionInput = document.getElementById("profileRegion");

  const focusableSelector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  const state = {
    activeModal: null,
    previouslyFocused: null,
    focusable: [],
  };

  function setModalVisibility(modal, visible) {
    if (!modal) return;
    if (visible) {
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
    } else {
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
    }
  }

  function getFocusable(modal) {
    if (!modal) return [];
    return Array.from(modal.querySelectorAll(focusableSelector)).filter(
      (el) => !el.hasAttribute("hidden") && el.offsetParent !== null
    );
  }

  function enforceFocus(event) {
    if (!state.activeModal) return;
    if (state.activeModal.contains(event.target)) return;
    if (state.focusable.length > 0) {
      state.focusable[0].focus();
    } else {
      state.activeModal.focus({ preventScroll: true });
    }
    event.stopPropagation();
    event.preventDefault();
  }

  function handleKeydown(event) {
    if (!state.activeModal) return;

    if (event.key === "Tab") {
      if (state.focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = state.focusable[0];
      const last = state.focusable[state.focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
        return;
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
        return;
      }
    }

    if (event.key === "Escape") {
      if (state.activeModal === profileModal) {
        hideProfileModal();
      }
      event.stopPropagation();
    }
  }

  function activateFocusTrap(modal) {
    state.activeModal = modal;
    state.previouslyFocused = document.activeElement;
    state.focusable = getFocusable(modal);
    if (state.focusable.length > 0) {
      state.focusable[0].focus({ preventScroll: true });
    } else {
      modal.setAttribute("tabindex", "-1");
      modal.focus({ preventScroll: true });
    }
    document.addEventListener("focus", enforceFocus, true);
    document.addEventListener("keydown", handleKeydown, true);
  }

  function deactivateFocusTrap() {
    document.removeEventListener("focus", enforceFocus, true);
    document.removeEventListener("keydown", handleKeydown, true);
    const { previouslyFocused } = state;
    state.activeModal = null;
    state.focusable = [];
    state.previouslyFocused = null;
    if (previouslyFocused && typeof previouslyFocused.focus === "function") {
      previouslyFocused.focus({ preventScroll: true });
    }
  }

  function showLoginModal() {
    setModalVisibility(profileModal, false);
    setModalVisibility(loginModal, true);
    activateFocusTrap(loginModal);
    if (loginEmailInput) {
      loginEmailInput.focus({ preventScroll: true });
    }
    console.log("evt=ui_modal_show", { which: "login" });
  }

  function hideLoginModal() {
    setModalVisibility(loginModal, false);
    deactivateFocusTrap();
  }

  function showProfileModal() {
    setModalVisibility(loginModal, false);
    setModalVisibility(profileModal, true);
    activateFocusTrap(profileModal);
    if (profileNameInput) {
      profileNameInput.focus({ preventScroll: true });
    }
    console.log("evt=ui_modal_show", { which: "profile" });
  }

  function hideProfileModal() {
    setModalVisibility(profileModal, false);
    deactivateFocusTrap();
  }

  function readCookie(name) {
    const cookie = document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${name}=`));
    if (!cookie) return null;
    return decodeURIComponent(cookie.split("=")[1]);
  }

  function getCsrfToken() {
    const candidates = ["askchip_csrf", "csrftoken", "csrf_token"];
    for (const name of candidates) {
      const value = readCookie(name);
      if (value) return value;
    }
    return null;
  }

  async function fetchCurrentUser() {
    try {
      const response = await fetch("/api/v1/auth/me", {
        method: "GET",
        credentials: "include",
      });
      if (response.status === 401) {
        showLoginModal();
        return;
      }
      if (!response.ok) {
        showLoginModal();
        return;
      }
      const payload = await response.json();
      if (payload && payload.profile_complete === false) {
        if (payload.email && profileEmailInput) {
          profileEmailInput.value = payload.email;
        }
        showProfileModal();
      }
    } catch (err) {
      console.warn("Failed to fetch current user", err);
      showLoginModal();
    }
  }

  async function handleLoginSubmit(event) {
    event.preventDefault();
    if (!loginEmailInput || !loginEmailInput.value) {
      return;
    }
    const csrfToken = getCsrfToken();
    const payload = { email: loginEmailInput.value };
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "x-csrf-token": csrfToken } : {}),
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        return;
      }
      const result = await response.json();
      if (result && result.next === "profile") {
        if (profileEmailInput) {
          profileEmailInput.value = loginEmailInput.value;
        }
        hideLoginModal();
        showProfileModal();
      } else if (result && result.next === "ready") {
        hideLoginModal();
      }
    } catch (err) {
      console.warn("Failed to login", err);
    }
  }

  async function handleProfileSubmit(event) {
    event.preventDefault();
    if (!profileNameInput || !profileTitleInput || !profileRegionInput) {
      return;
    }
    const csrfToken = getCsrfToken();
    const payload = {
      name: profileNameInput.value,
      title: profileTitleInput.value,
      region: profileRegionInput.value,
    };
    try {
      const response = await fetch("/api/v1/auth/profile", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "x-csrf-token": csrfToken } : {}),
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        return;
      }
      hideProfileModal();
    } catch (err) {
      console.warn("Failed to save profile", err);
    }
  }

  if (loginForm) {
    loginForm.addEventListener("submit", handleLoginSubmit);
  }
  if (profileForm) {
    profileForm.addEventListener("submit", handleProfileSubmit);
  }

  document.addEventListener("DOMContentLoaded", () => {
    fetchCurrentUser();
  });
})();
