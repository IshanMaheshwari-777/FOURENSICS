import type { Claim, PanelResult, Technical } from "../types";

export type Filter = "all" | Technical;
export type RunState = "running" | "partial" | "complete" | "error";

// Presentation is derived from extracted claims, never from repository names or
// issue labels. Unassessed claims stay unresolved once a run has stopped.
export function present(result: PanelResult | null, error: string | null) {
  const state: RunState = error || result?.status === "error" ? "error"
    : !result || result.status === "queued" || result.status === "running" ? "running"
    : result.status === "partial" ? "partial" : "complete";
  const claims = [...(result?.claims ?? [])].sort((x, y) => Number(y.is_primary) - Number(x.is_primary));
  const assessments = new Map((result?.assessments ?? []).map((a) => [a.claim_id, a]));
  const outcome = (claim: Claim): Technical => assessments.get(claim.id)?.technical ?? "inconclusive";
  const counts = { supported: 0, contradicted: 0, inconclusive: 0 };
  for (const claim of claims) counts[outcome(claim)]++;
  const executions = [...assessments.values()].filter((a) => claims.some((c) => c.id === a.claim_id));
  const reproduced = executions.filter((a) => a.verification === "reproduced").length;
  const notReproduced = executions.filter((a) => a.verification === "not_reproduced").length;
  const unavailable = executions.some((a) => a.verification === "environment_unavailable");
  const executionRecorded = result?.evidence.some((e) => e.tier === "E1");
  const execution = reproduced || notReproduced
    ? [reproduced && `${reproduced} claim${reproduced === 1 ? "" : "s"} reproduced`, notReproduced && `${notReproduced} claim${notReproduced === 1 ? "" : "s"} not reproduced`].filter(Boolean).join(" · ")
    : unavailable ? "Environment unavailable" : executionRecorded ? "Execution recorded; see details"
    : state === "running" ? "No execution result yet" : "Not run";
  const blockers = (result?.blockers ?? []).filter((b, i, all) =>
    all.findIndex((other) => other.reason === b.reason && other.next_step === b.next_step) === i);
  return { state, claims, assessments, outcome, counts, execution, blockers };
}

// Saved assessments and contributor-controlled text must not become executable links.
export function safeHref(value?: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

export function sourceLocation(path?: string | null, start?: number | null, end?: number | null): string {
  return `${path || "Source"}${start ? `:${start}${end && end !== start ? `–${end}` : ""}` : ""}`;
}
