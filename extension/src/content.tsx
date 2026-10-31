// Content script: detects GitHub issue and PR pages, mounts the panel in a Shadow DOM,
// and polls the backend (through the service worker) until the analysis finishes.
import { createRoot, type Root } from "react-dom/client";
import { Panel } from "./panel/Panel";
import { css } from "./panel/styles";
import type { PanelResult, Reply, Request, Settings } from "./types";

const PAGE = /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(issues|pull)\/(\d+)(?:[/?#]|$)/;
const POLL_MS = 2000;
const SLOW_AFTER_MS = 60 * 1000;
const SLOW_POLL_MS = 5000;
const MAX_POLL_MS = 15 * 60 * 1000;
const HOST_ID = "evidence-triage-host";

let root: Root | null = null;
let host: HTMLElement | null = null;
let currentKey: string | null = null;
let generation = 0; // invalidates polling loops when the page changes
let rerender: (() => void) | null = null; // redraws the current panel, e.g. after a theme switch
const hidden = new Set<string>();

function send<T>(msg: Request): Promise<Reply<T>> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp: Reply<T> | undefined) => {
        if (chrome.runtime.lastError || !resp) {
          resolve({ ok: false, error: "The extension was reloaded. Refresh this page." });
        } else {
          resolve(resp);
        }
      });
    } catch {
      resolve({ ok: false, error: "The extension was reloaded. Refresh this page." });
    }
  });
}

// Follow what GitHub actually renders: its theme setting can differ from the OS setting.
function isDark(): boolean {
  const rgb = getComputedStyle(document.body).backgroundColor.match(/\d+(\.\d+)?/g)?.map(Number);
  if (rgb && rgb.length >= 3 && (rgb.length < 4 || rgb[3] > 0)) {
    const [r, g, b] = rgb;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
  }
  const mode = document.documentElement.getAttribute("data-color-mode");
  if (mode === "dark") return true;
  if (mode === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function pageKey(): { key: string; url: string } | null {
  const m = location.href.match(PAGE);
  if (!m) return null;
  const [, owner, repo, kind, number] = m;
  const url = `https://github.com/${owner}/${repo}/${kind}/${number}`;
  return { key: url, url };
}

function unmount(): void {
  root?.unmount();
  host?.remove();
  root = null;
  host = null;
}

function mount(): Root {
  if (root && host?.isConnected) return root;
  unmount();
  host = document.createElement("div");
  host.id = HOST_ID;
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  shadow.appendChild(style);
  const container = document.createElement("div");
  shadow.appendChild(container);
  document.body.appendChild(host);
  root = createRoot(container);
  return root;
}

async function run(url: string, key: string, refresh = false): Promise<void> {
  const gen = ++generation;
  const settingsReply = await send<Settings>({ type: "settings" });
  const startCollapsed = settingsReply.ok ? settingsReply.result.collapsed : false;
  let result: PanelResult | null = null;
  let error: string | null = null;
  let notice: string | null = null;

  const render = () => {
    if (gen !== generation) return;
    mount().render(
      <Panel
        result={result}
        error={error}
        notice={notice}
        dark={isDark()}
        startCollapsed={startCollapsed}
        onRefresh={() => run(url, key, true)}
        onClose={() => { hidden.add(key); generation++; unmount(); }}
        onVote={(claimId, vote) => { if (result) void send({ type: "feedback", runId: result.run_id, claimId, vote }); }}
      />,
    );
  };

  rerender = render;
  render();
  const first = await send<PanelResult>({ type: "assess", url, refresh });
  if (!first.ok) {
    error = first.error;
    render();
    return;
  }
  result = first.result;
  render();

  const started = Date.now();
  let failures = 0;
  while (gen === generation && result && (result.status === "queued" || result.status === "running")) {
    const waited = Date.now() - started;
    if (waited > MAX_POLL_MS) {
      notice = "Still not finished. Use ↻ to check again later; the analysis keeps running in the background.";
      render();
      return;
    }
    // A busy or rate-limited model server can take minutes: say so calmly and poll less often.
    if (waited > SLOW_AFTER_MS) {
      notice = "Still working. The model server is busy, so this can take a few minutes.";
    }
    await new Promise((r) => setTimeout(r, waited > SLOW_AFTER_MS ? SLOW_POLL_MS : POLL_MS));
    const next: Reply<PanelResult> = await send<PanelResult>({ type: "run", runId: result.run_id });
    if (!next.ok) {
      if (++failures >= 3) {  // ride out a backend restart or a brief network blip
        error = next.error;
        render();
        return;
      }
      continue;
    }
    failures = 0;
    result = next.result;
    if (result.status !== "queued" && result.status !== "running") notice = null;
    render();
  }
}

function check(): void {
  const page = pageKey();
  if (!page) {
    if (currentKey) {
      currentKey = null;
      generation++;
      unmount();
    }
    return;
  }
  if (page.key === currentKey && host?.isConnected) return;
  if (hidden.has(page.key)) return;
  currentKey = page.key;
  void run(page.url, page.key);
}

// GitHub navigates without full page loads, so watch for URL changes several ways.
document.addEventListener("turbo:load", check);
window.addEventListener("popstate", check);
let lastHref = location.href;
setInterval(() => {
  if (location.href !== lastHref) {
    lastHref = location.href;
    check();
  } else if (currentKey && !host?.isConnected && !hidden.has(currentKey)) {
    currentKey = null; // GitHub replaced the body; re-mount
    check();
  }
}, 1000);

// Redraw when GitHub's theme or the OS theme changes.
new MutationObserver(() => rerender?.()).observe(document.documentElement, {
  attributes: true, attributeFilter: ["data-color-mode", "data-light-theme", "data-dark-theme", "class"],
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => rerender?.());
check();
