// Renders the panel with the bundled mock result, outside GitHub, for UI work.
import { createRoot } from "react-dom/client";
import saved from "../../eval/examples/panel_google_verified_pr.json";
import { scenario } from "./scenarios";
import { Idle, Panel } from "../src/panel/Panel";
import { css } from "../src/panel/styles";
import type { PanelResult } from "../src/types";

// Docked on the right like Chrome's side panel; ?width= tries other panel widths.
const host = document.createElement("div");
const panelWidth = Number(new URLSearchParams(location.search).get("width")) || 400;
host.style.cssText = `position:fixed;top:0;right:0;bottom:0;width:min(${panelWidth}px,100vw);border-left:1px solid rgba(127,127,140,.3);box-shadow:-8px 0 24px rgba(0,0,0,.06);z-index:10`;
document.body.style.marginRight = `min(${panelWidth}px,100vw)`;
const shadow = host.attachShadow({ mode: "open" });
const style = document.createElement("style");
style.textContent = css;
const container = document.createElement("div");
container.style.height = "100%";
shadow.append(style, container);
document.body.appendChild(host);
const root = createRoot(container);

const complete: PanelResult = scenario("complete");
const running: PanelResult = {
  ...complete, status: "running", summary_line: "Reference checks done; interpreting claims", summary_tone: "pending",
  claims: [], assessments: [], stages: complete.stages.slice(0, 6),
};
let dark = false;
let state: "complete" | "running" | "error" | "live" | "fixture" | "idle" = "complete";
let fixture = complete;
let live: PanelResult | null = null;
let errorMessage = "Can't reach the analysis backend. Is it running?";
let generation = 0;

async function loadLive() {
  const gen = ++generation;
  (document.getElementById("scenario") as HTMLSelectElement).value = "";
  const url = (document.getElementById("live-url") as HTMLInputElement).value;
  const base = (document.getElementById("backend-url") as HTMLInputElement).value.replace(/\/+$/, "");
  live = null; state = "live";
  document.getElementById("source")!.textContent = `Loading assessment: ${url}`;
  render();
  try {
    async function get(path: string): Promise<PanelResult> {
      const response = await fetch(base + path, { signal: AbortSignal.timeout(20000) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? `HTTP ${response.status}`);
      return body as PanelResult;
    }
    let r = await get(`/api/assessment?url=${encodeURIComponent(url)}`);
    const deadline = Date.now() + 15 * 60 * 1000;
    while (gen === generation && (r.status === "queued" || r.status === "running")) {
      live = r; state = "live"; render();
      if (Date.now() > deadline) throw new Error("Still running; inspect the backend run later.");
      await new Promise((res) => setTimeout(res, 2000));
      r = await get(`/api/runs/${r.run_id}`);
    }
    if (gen !== generation) return;
    live = r; state = "live";
    document.getElementById("source")!.textContent = `Live assessment: ${r.target?.url ?? url}`;
  } catch (error) {
    if (gen !== generation) return;
    errorMessage = error instanceof Error ? error.message : String(error);
    state = "error";
  }
  render();
}

function render() {
  document.body.classList.toggle("dark", dark);
  if (state === "idle") {
    root.render(<Idle dark={dark} onSettings={() => console.log("settings")} />);
    return;
  }
  root.render(
    <Panel
      key={state + (state === "fixture" ? fixture.run_id : live?.run_id ?? "")}
      result={state === "error" ? null : state === "running" ? running : state === "live" ? live : state === "fixture" ? fixture : complete}
      error={state === "error" ? errorMessage : null}
      dark={dark}
      onRefresh={() => render()}
      onSettings={() => console.log("settings")}
      onVote={(c, v) => console.log("vote", c, v)}
    />,
  );
}

document.querySelectorAll<HTMLButtonElement>("button[data-state]").forEach((b) =>
  b.addEventListener("click", () => {
    generation++; state = b.dataset.state as typeof state;
    (document.getElementById("scenario") as HTMLSelectElement).value = "";
    document.getElementById("source")!.textContent = "Showing a bundled mock state, not a live assessment.";
    render();
  }));
document.getElementById("theme")!.addEventListener("click", () => { dark = !dark; render(); });
function chooseScenario(name: string) {
  generation++;
  if (name === "saved") {
    live = saved as unknown as PanelResult; state = "live";
    document.getElementById("source")!.textContent = `Saved API assessment; not a new analysis: ${live.target?.url}; pipeline ${live.pipeline_version}; run ${live.run_id}`;
  } else {
    fixture = scenario(name); state = "fixture";
    document.getElementById("source")!.textContent = `Synthetic presentation fixture: ${name}. No inference or GitHub requests.`;
  }
  render();
}
document.getElementById("scenario")!.addEventListener("change", (event) => {
  chooseScenario((event.target as HTMLSelectElement).value);
});
document.getElementById("live")!.addEventListener("click", () => { void loadLive(); });
document.getElementById("saved")!.addEventListener("change", async (event) => {
  generation++;
  (document.getElementById("scenario") as HTMLSelectElement).value = "";
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const result = JSON.parse(await file.text()) as PanelResult;
    if (!result.run_id || !Array.isArray(result.claims) || !Array.isArray(result.assessments) || !Array.isArray(result.evidence)) {
      throw new Error("Choose a saved PanelResult JSON assessment.");
    }
    live = result; state = "live";
    document.getElementById("source")!.textContent = `Saved assessment (not a new analysis): ${result.target?.url ?? file.name}; pipeline ${result.pipeline_version}`;
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : String(error);
    state = "error";
  }
  render();
});
render();

// Explicit demo links can restore the chosen API and issue without changing extension settings.
const launch = new URLSearchParams(location.search);
const launchScenario = launch.get("scenario");
if (launch.get("theme") === "dark") dark = true;
if (launchScenario && ["complete", "saved", "partial", "long", "reproduced", "empty", "feature"].includes(launchScenario)) {
  (document.getElementById("scenario") as HTMLSelectElement).value = launchScenario;
  chooseScenario(launchScenario);
} else render();
const launchBackend = launch.get("backend");
const launchUrl = launch.get("url");
if (launchBackend && launchUrl) {
  (document.getElementById("backend-url") as HTMLInputElement).value = launchBackend;
  (document.getElementById("live-url") as HTMLInputElement).value = launchUrl;
  void loadLive();
}
