// Content script on github.com. The analysis lives in Chrome's side panel; this script only
//  1. tells the extension which issue or PR the tab shows (GitHub navigates without page loads),
//  2. inserts a reply draft into the comment box when the panel asks (never submits), and
//  3. shows a slim launcher tab on issue and PR pages that opens the side panel.
import { insertReply } from "./panel/reply";
import type { InsertReply, Reply, Request } from "./types";

const PAGE = /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(issues|pull)\/(\d+)(?:[/?#]|$)/;
const LAUNCHER_ID = "fourensics-launcher";

function currentPage(): string | null {
  const m = location.href.match(PAGE);
  if (!m) return null;
  const [, owner, repo, kind, number] = m;
  return `https://github.com/${owner}/${repo}/${kind}/${number}`;
}

function send<T>(msg: Request): Promise<Reply<T>> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp: Reply<T> | undefined) => {
        if (chrome.runtime.lastError || !resp) resolve({ ok: false, error: "The extension was reloaded. Refresh this page." });
        else resolve(resp);
      });
    } catch {
      resolve({ ok: false, error: "The extension was reloaded. Refresh this page." });
    }
  });
}

const LAUNCHER_CSS = `
:host { all: initial; }
button { all: unset; box-sizing: border-box; position: fixed; right: 0; top: 42%; z-index: 2147483000; cursor: pointer;
  display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 10px 0 7px; border-radius: 10px 0 0 10px;
  background: #171717; color: #fafafa; box-shadow: 0 6px 20px rgba(0,0,0,.28), inset 0 0 0 1px rgba(255,255,255,.1);
  font: 600 12.5px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: .02em;
  transform: translateX(calc(100% - 38px)); transition: transform .18s ease; }
button:hover, button:focus-visible { transform: translateX(0); }
button:focus-visible { outline: 2px solid #a3a3a3; outline-offset: 2px; }
.mark { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 6px; background: #fafafa; color: #171717; font-weight: 800; }
.hint { position: fixed; right: 12px; top: calc(42% + 44px); z-index: 2147483000; max-width: 240px; padding: 8px 10px; border-radius: 8px;
  background: #171717; color: #f5f5f5; font: 12.5px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; box-shadow: 0 6px 20px rgba(0,0,0,.25); }
@media (prefers-reduced-motion: reduce) { button { transition: none; } }
`;

function showLauncher(show: boolean): void {
  const existing = document.getElementById(LAUNCHER_ID);
  if (!show) { existing?.remove(); return; }
  if (existing?.isConnected) return;
  const host = document.createElement("div");
  host.id = LAUNCHER_ID;
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = LAUNCHER_CSS;
  const button = document.createElement("button");
  button.type = "button";
  button.title = "Open FOURENSICS";
  button.setAttribute("aria-label", "Open FOURENSICS side panel");
  button.innerHTML = `<span class="mark" aria-hidden="true">F</span><span>FOURENSICS</span>`;
  button.addEventListener("click", async () => {
    const reply = await send<null>({ type: "open-panel" });
    if (reply.ok) return;
    // Older Chrome, or the click was not accepted: point to the toolbar icon instead.
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.setAttribute("role", "status");
    hint.textContent = reply.error.includes("reloaded")
      ? reply.error
      : "Click the FOURENSICS icon in Chrome's toolbar (puzzle-piece menu → pin it) to open the side panel.";
    shadow.appendChild(hint);
    setTimeout(() => hint.remove(), 6000);
  });
  shadow.append(style, button);
  document.body.appendChild(host);
}

let lastReported: string | null | undefined;
function check(): void {
  const page = currentPage();
  showLauncher(page !== null);
  if (page === lastReported) return;
  lastReported = page;
  void send({ type: "page", url: page });
}

chrome.runtime.onMessage.addListener((msg: InsertReply, _sender, sendResponse) => {
  if (msg?.type !== "insert-reply") return false;
  insertReply(msg.text).then(sendResponse);
  return true;
});

// GitHub navigates without full page loads, so watch for URL changes several ways.
document.addEventListener("turbo:load", check);
window.addEventListener("popstate", check);
window.addEventListener("pageshow", () => { lastReported = undefined; check(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
let lastHref = location.href;
setInterval(() => {
  if (location.href !== lastHref) { lastHref = location.href; check(); }
  else if (currentPage() && !document.getElementById(LAUNCHER_ID)) showLauncher(true); // GitHub replaced the body
}, 1000);
check();
