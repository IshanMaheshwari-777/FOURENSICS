const DEFAULTS = { backendUrl: "http://localhost:8787", mock: false, collapsed: false };
const $ = (id) => document.getElementById(id);

chrome.storage.sync.get(DEFAULTS, (s) => {
  $("backend").value = s.backendUrl;
  $("mock").checked = s.mock;
  $("collapsed").checked = s.collapsed;
});

$("save").addEventListener("click", async () => {
  const backendUrl = ($("backend").value || DEFAULTS.backendUrl).trim().replace(/\/+$/, "");
  let origin;
  try {
    origin = new URL(backendUrl).origin;
  } catch {
    $("status").textContent = "That is not a valid URL.";
    return;
  }
  const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
  if (!isLocal) {
    const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    if (!granted) {
      $("status").textContent = "Permission for that host was not granted.";
      return;
    }
  }
  chrome.storage.sync.set(
    { backendUrl, mock: $("mock").checked, collapsed: $("collapsed").checked },
    () => ($("status").textContent = "Saved.")
  );
});
