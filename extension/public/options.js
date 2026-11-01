const DEFAULTS = { backendUrl: "http://localhost:8787", mock: false };
const $ = (id) => document.getElementById(id);
const status = (text, kind = "") => { $("status").textContent = text; $("status").className = kind; };

chrome.storage.sync.get(DEFAULTS, (s) => {
  $("backend").value = s.backendUrl;
  $("mock").checked = s.mock;
});

$("save").addEventListener("click", async () => {
  const backendUrl = ($("backend").value || DEFAULTS.backendUrl).trim().replace(/\/+$/, "");
  let origin;
  try {
    origin = new URL(backendUrl).origin;
  } catch {
    status("That is not a valid URL.", "bad");
    return;
  }
  const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
  if (!isLocal) {
    const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    if (!granted) {
      status("Permission for that host was not granted.", "bad");
      return;
    }
  }
  chrome.storage.sync.set({ backendUrl, mock: $("mock").checked }, () => status("Saved. Reopen the side panel to apply.", "ok"));
});
