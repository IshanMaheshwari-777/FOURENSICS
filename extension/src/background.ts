// Service worker: the only place that talks to the backend. Content scripts send
// messages here, which keeps network permissions narrow and avoids CORS issues.
import mockResult from "../mock/panel_result.example.json";
import type { PanelResult, Reply, Request, Settings } from "./types";

const DEFAULTS: Settings = { backendUrl: "http://localhost:8787", mock: false, collapsed: false };
const TIMEOUT_MS = 20_000;

async function settings(): Promise<Settings> {
  return (await chrome.storage.sync.get(DEFAULTS)) as Settings;
}

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

async function handle(msg: Request): Promise<Reply<unknown>> {
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
  }
}

chrome.runtime.onMessage.addListener((msg: Request, _sender, sendResponse) => {
  handle(msg)
    .then(sendResponse)
    .catch((e: unknown) => sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) }));
  return true; // keep the channel open for the async reply
});
