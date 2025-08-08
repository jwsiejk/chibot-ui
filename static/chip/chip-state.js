// chip-state.js – Manages Chip's local memory (browser-side)

export function getProfile() {
  return {
    name: localStorage.getItem("profileName") || "",
    title: localStorage.getItem("profileTitle") || ""
  };
}

export function setProfile(name, title) {
  localStorage.setItem("profileName", name);
  localStorage.setItem("profileTitle", title);
  localStorage.setItem("chip_name", name);
  localStorage.setItem("chip_title", title);
}

export function getLoginEmail() {
  return localStorage.getItem("chip_login") || "";
}

export function setLoginEmail(email) {
  localStorage.setItem("chip_login", email);
}
