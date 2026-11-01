// Chrome side panel: follows the active tab, and when it shows a GitHub issue or PR, runs the
// assessment (through the service worker) and polls until it finishes.
import { createRoot } from "react-dom/client";
import { Idle, Panel } from "./panel/Panel";
import { css } from "./panel/styles";
import type { InsertReply, PanelResult, Reply, Request } from "./types";

const POLL_MS = 2000;
const SLOW_AFTER_MS = 60 * 1000;
const SLOW_POLL_MS = 5000;
const MAX_POLL_MS = 15 * 60 * 1000;

const style = document.createElement("style");
style.textContent = css;
document.head.appendChild(style);
const root = createRoot(document.getElementById("root")!);

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
let tabId: number | null = null;
let target: string | null | undefined; // undefined: not known yet
let generation = 0; // invalidates polling loops when the tab or page changes
let rerender: () => void = () => undefined;

function send<T>(msg: Request): Promise<Reply<T>> {
  return chrome.runtime.sendMessage(msg).then(
    (resp: Reply<T> | undefined) => resp ?? { ok: false, error: "No response from the extension." },
    (e: unknown) => ({ ok: false, error: e instanceof Error ? e.message : String(e) }),
  );
}

const openSettings = () => { void chrome.runtime.openOptionsPage(); };

async function insertIntoTab(text: string): Promise<string> {
  if (tabId !== null) {
    try {
      const msg: InsertReply = { type: "insert-reply", text };
      const reply = await chrome.tabs.sendMessage(tabId, msg) as string | undefined;
      if (reply) return reply;
    } catch { /* the tab has no content script (e.g. opened before install); fall back below */ }
  }
  try {
    await navigator.clipboard.writeText(text);
    return "Copied. Paste it into the comment box.";
  } catch {
    return "Couldn't reach the comment box. Reload the GitHub tab and try again.";
  }
}

function idle(message: string | null = null): void {
  generation++;
  rerender = () => root.render(<Idle dark={darkQuery.matches} message={message} onSettings={openSettings} />);
  rerender();
}

async function run(url: string, refresh = false): Promise<void> {
  const gen = ++generation;
  let result: PanelResult | null = null;
  let error: string | null = null;
  let notice: string | null = null;

  const render = () => {
    if (gen !== generation) return;
    root.render(
      <Panel
        key={url}
        result={result}
        error={error}
        notice={notice}
        dark={darkQuery.matches}
        onRefresh={() => void run(url, true)}
        onSettings={openSettings}
        onInsertReply={insertIntoTab}
        onVote={(claimId, vote) => { if (result) void send({ type: "feedback", runId: result.run_id, claimId, vote }); }}
      />,
    );
  };
  rerender = render;
  render();

  const first = await send<PanelResult>({ type: "assess", url, refresh });
  if (gen !== generation) return;
  if (!first.ok) { error = first.error; render(); return; }
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
    if (waited > SLOW_AFTER_MS) notice = "Still working. The model server is busy, so this can take a few minutes.";
    await new Promise((r) => setTimeout(r, waited > SLOW_AFTER_MS ? SLOW_POLL_MS : POLL_MS));
    if (gen !== generation) return;
    const next: Reply<PanelResult> = await send<PanelResult>({ type: "run", runId: result.run_id });
    if (gen !== generation) return;
    if (!next.ok) {
      if (++failures >= 3) { error = next.error; render(); return; } // ride out a backend restart or a brief blip
      continue;
    }
    failures = 0;
    result = next.result;
    if (result.status !== "queued" && result.status !== "running") notice = null;
    render();
  }
}

async function follow(): Promise<void> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab?.id ?? null;
  const reply = tabId === null ? null : await send<{ url: string | null }>({ type: "page-for-tab", tabId });
  const url = reply?.ok ? reply.result.url : null;
  if (url === target) return;
  target = url;
  if (url) void run(url);
  // A tab opened before the extension was installed or updated has no content script yet.
  else idle("Already on an issue or pull request? Reload that tab once after installing or updating FOURENSICS.");
}

chrome.tabs.onActivated.addListener(() => { void follow(); });
chrome.windows.onFocusChanged.addListener(() => { void follow(); });
chrome.runtime.onMessage.addListener((msg: { type?: string; tabId?: number }) => {
  if (msg?.type === "page-changed" && msg.tabId === tabId) void follow();
  return false;
});
darkQuery.addEventListener("change", () => rerender());
idle();
void follow();
