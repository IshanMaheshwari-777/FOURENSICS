// Service worker: the only place that talks to the backend, and the link between GitHub tabs
// (content script) and Chrome's side panel. Keeps network permissions narrow and avoids CORS.
import mockResult from "../mock/panel_result.example.json";
import type { PageChanged, PanelResult, Reply, Request, Settings } from "./types";

const DEFAULTS: Settings = { backendUrl: "http://localhost:8787", mock: false };
const TIMEOUT_MS = 20_000;

// The toolbar icon opens the side panel.
void chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => undefined);

async function settings(): Promise<Settings> {
  return (await chrome.storage.sync.get(DEFAULTS)) as Settings;
}

// Which issue or PR each tab shows. Session storage survives service-worker restarts.
const pageKey = (tabId: number) => `page:${tabId}`;
async function setPage(tabId: number, url: string | null): Promise<void> {
  const key = pageKey(tabId);
  const before = (await chrome.storage.session.get(key))[key] ?? null;
  if (url) await chrome.storage.session.set({ [key]: url });
  else await chrome.storage.session.remove(key);
  if (before !== url) {
    const msg: PageChanged = { type: "page-changed", tabId };
    chrome.runtime.sendMessage(msg).catch(() => undefined); // no side panel open: nobody listens
  }
}
async function getPage(tabId: number): Promise<string | null> {
  const key = pageKey(tabId);
  return (await chrome.storage.session.get(key))[key] ?? null;
}
chrome.tabs.onRemoved.addListener((tabId) => { void chrome.storage.session.remove(pageKey(tabId)); });

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const { backendUrl } = await settings();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(backendUrl.replace(/\/+$/, "") + path, { ...init, signal: ctrl.signal });
  } catch {
    throw new Error(`Can't reach the analysis backend at ${backendUrl}. Is it running?`);
  } finally {
    clearTimeout(timer);
  }
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`Backend error: ${detail}`);
  }
  return (await resp.json()) as T;
}

async function handle(msg: Request, sender: chrome.runtime.MessageSender): Promise<Reply<unknown>> {
  if (msg.type === "page") {
    if (sender.tab?.id !== undefined) await setPage(sender.tab.id, msg.url);
    return { ok: true, result: null };
  }
  if (msg.type === "page-for-tab") return { ok: true, result: { url: await getPage(msg.tabId) } };
  const s = await settings();
  if (msg.type === "settings") return { ok: true, result: s };
  if (s.mock && msg.type !== "feedback") {
    const result: PanelResult = { ...(mockResult as unknown as PanelResult), mock: true };
    return { ok: true, result };
  }
  switch (msg.type) {
    case "assess": {
      const q = new URLSearchParams({ url: msg.url });
      if (msg.refresh) q.set("refresh", "true");
      return { ok: true, result: await call<PanelResult>(`/api/assessment?${q}`) };
    }
    case "run":
      return { ok: true, result: await call<PanelResult>(`/api/runs/${encodeURIComponent(msg.runId)}`) };
    case "feedback":
      if (s.mock) return { ok: true, result: null };
      await call("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: msg.runId, claim_id: msg.claimId, vote: msg.vote }),
      });
      return { ok: true, result: null };
    default:
      return { ok: false, error: "Unknown request" };
  }
}

chrome.runtime.onMessage.addListener((msg: Request | PageChanged, sender, sendResponse) => {
  if (msg.type === "page-changed") return false; // our own broadcast to the side panel
  if (msg.type === "open-panel") {
    // Must run before any await so Chrome still sees the user's click.
    const tabId = sender.tab?.id;
    if (tabId === undefined || !chrome.sidePanel?.open) {
      sendResponse({ ok: false, error: "unavailable" });
      return false;
    }
    chrome.sidePanel.open({ tabId })
      .then(() => sendResponse({ ok: true, result: null }))
      .catch((e: unknown) => sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) }));
    return true;
  }
  handle(msg, sender)
    .then(sendResponse)
    .catch((e: unknown) => sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) }));
  return true; // keep the channel open for the async reply
});
