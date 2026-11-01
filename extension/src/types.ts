// Mirrors backend/app/schemas.py PanelResult. backend/tests/test_api.py validates
// mock/panel_result.example.json against the backend model so the two cannot drift.

export type Tier = "E1" | "E2" | "E3" | "E4";
export type Technical = "supported" | "contradicted" | "inconclusive";
export type Verification = "reproduced" | "not_reproduced" | "not_attempted" | "environment_unavailable";
export type Relation = "supports" | "contradicts" | "context";
export type RunStatus = "queued" | "running" | "partial" | "complete" | "error";
export type Tone = "supported" | "contradicted" | "mixed" | "neutral" | "not_defect" | "pending";

export interface Target { owner: string; repo: string; kind: "issue" | "pr"; number: number; url: string }

export interface Snapshot {
  sha: string;
  method: "exact" | "release" | "date" | "pr" | "unknown";
  reliability: "exact" | "release-matched" | "inferred" | "unknown";
  ref_label: string;
  base_sha?: string | null;
  head_sha?: string | null;
}

export interface Anchor { kind: string; value: string; line?: number | null; func?: string | null }

export interface Claim {
  id: string;
  text: string;
  type: string;
  is_primary: boolean;
  source_span: string;
  inferred: boolean;
  anchors: Anchor[];
  condition?: string | null;
  expected?: string | null;
  observed?: string | null;
}

export interface EvidenceItem {
  id: string;
  claim_ids: string[];
  kind: string;
  tier: Tier;
  sha: string;
  path?: string | null;
  start?: number | null;
  end?: number | null;
  excerpt?: string | null;
  content_hash?: string | null;
  result: string;
  raw: string;
  permalink?: string | null;
  absence: boolean;
}

export interface ClaimAssessment {
  claim_id: string;
  technical: Technical;
  verification: Verification;
  evidence_ids: string[];
  relations: Record<string, Relation>;
  explanation: string;
  limitations: string[];
  strength: "high" | "medium" | "low" | "none";
  lean?: "supported" | "contradicted" | null;
  decided_by: "rule" | "model" | "none";
  downgraded_reason?: string | null;
  unconfirmed_reasoning?: string | null;
}

export interface StageResult { stage: string; status: "ok" | "degraded" | "failed" | "skipped"; reason?: string | null; ms: number }

export interface PanelResult {
  run_id: string;
  status: RunStatus;
  target?: Target | null;
  report_kind?: string | null;
  stages: StageResult[];
  snapshot: Snapshot;
  summary_line: string;
  summary_tone: Tone;
  claims: Claim[];
  assessments: ClaimAssessment[];
  evidence: EvidenceItem[];
  missing_info: string[];
  /** Report wording signals (codes); used by evaluation, not shown. */
  report_signals?: string[];
  /** Triage label, present only when code or execution evidence backs it (basis "evidence"). */
  suggested_label?: { label: "confirmed" | "invalid" | "cannot_reproduce"; basis: "evidence" | "leaning"; reasons: string[] } | null;
  reply_draft?: string | null;
  not_checked: string[];
  notes: string[];
  blockers?: { code: string; stage: string; reason: string; next_step: string }[];
  pipeline_version: string;
  models: Record<string, string>;
  error?: string | null;
  /** Set by the extension itself in mock mode; never sent by the backend. */
  mock?: boolean;
}

export type Request =
  | { type: "assess"; url: string; refresh?: boolean }
  | { type: "run"; runId: string }
  | { type: "feedback"; runId: string; claimId: string; vote: "yes" | "no" }
  | { type: "settings" };

export type Reply<T = PanelResult> = { ok: true; result: T } | { ok: false; error: string };

export interface Settings { backendUrl: string; mock: boolean; collapsed: boolean }
