import mock from "../mock/panel_result.example.json";
import type { PanelResult } from "../src/types";

// Offline presentation fixtures only. These do not assert findings about real repos.
const example: PanelResult = { ...(mock as unknown as PanelResult), mock: true };
export function scenario(name: string): PanelResult {
  const r = structuredClone(example);
  r.run_id = `preview-${name}`;
  if (name === "partial") {
    r.status = "partial"; r.summary_tone = "neutral";
    r.summary_line = "Source evidence was collected; claim verification could not finish.";
    r.assessments = [];
    r.stages = [{ stage: "retrieval", status: "ok", ms: 10 }, { stage: "adjudication", status: "degraded", ms: 10, reason: "Inference quota unavailable" }];
    r.blockers = [{ code: "model_quota", stage: "adjudication", reason: "The inference service has no remaining free quota for this run.", next_step: "Retry when the provider quota resets, or configure another supported open-weight endpoint. The report itself may already contain enough information." }];
    r.missing_info = [];
    r.suggested_label = null;
  }
  if (name === "long") {
    r.target = { owner: "public-organization-with-a-long-name", repo: "multi-language-monorepo", kind: "pr", number: 12345, url: "https://github.com/example/monorepo/pull/12345" };
    r.snapshot.reliability = "inferred";
    r.snapshot.ref_label = "release/candidate-for-cross-platform-validation";
    r.summary_tone = "neutral";
    r.summary_line = "The diff is relevant, but the reported cross-platform behavior still needs a reproduction.";
    r.claims = [{ ...r.claims[0], text: "The asynchronous cancellation path in `HandleConnection` leaks a resource when a client disconnects during a concurrent retry on Windows, Linux, or macOS.", expected: "The resource is released before the worker exits.", observed: "The reporter describes an increasing resource count.", condition: "A disconnect overlaps a retry." }];
    r.assessments = [{ ...r.assessments[0], technical: "inconclusive", strength: "none", evidence_ids: ["E-long"], explanation: "The source shows a cancellation branch, but inspecting that branch does not establish a leak or reproduce the concurrent schedule.", limitations: ["No concurrency stress test was executed.", "The inspected version is approximate; the reporter did not identify a commit."], lean: "supported", downgraded_reason: "Source context cannot settle the reported runtime behavior." }];
    r.evidence = [{ ...r.evidence[0], id: "E-long", tier: "E3", path: "packages/networking/clients/platform-specific/多言語/" + "long-component-name-".repeat(9) + ".go", result: "The cancellation handler calls `releaseConnection`, whose ownership depends on the surrounding retry path.", excerpt: "// This is synthetic source text for layout testing.\nfunc HandleConnection(ctx context.Context) {\n    // " + "a-long-source-token".repeat(40) + "\n}\n<script>alert('text only')</script>", permalink: "https://github.com/example/monorepo/blob/abc/src/client.go#L12-L20" }];
    r.missing_info = ["Exact commit or release", "A minimal reproduction with the concurrent schedule and expected resource count"];
    r.reply_draft = "Could you share the exact version and a minimal reproduction of the concurrent cancellation?";
    r.suggested_label = null;
  }
  if (name === "empty") {
    r.summary_line = "No technical claim could be extracted from this report.";
    r.summary_tone = "neutral"; r.claims = []; r.assessments = []; r.evidence = [];
    r.missing_info = ["A description of the observed behavior and the expected result"];
    r.suggested_label = null;
  }
  if (name === "feature") {
    r.report_kind = "feature_request"; r.summary_tone = "not_defect";
    r.summary_line = "This report requests a new configuration option.";
    r.claims = []; r.assessments = []; r.evidence = [];
    r.not_checked = ["Feature feasibility and product priority were not evaluated."];
    r.suggested_label = { label: "invalid", basis: "evidence", reasons: ["reads as a feature request, not a defect"] };
  }
  if (name === "reproduced") {
    r.summary_tone = "supported"; r.summary_line = "The reported exception was reproduced in the reviewed sandbox scenario.";
    r.claims = [r.claims[0]];
    r.assessments = [{ ...r.assessments[0], verification: "reproduced", evidence_ids: ["E-execution"], relations: { "E-execution": "supports" }, explanation: "The reviewed example raised the reported exception in two isolated runs.", limitations: ["This demonstrates the observation in the tested environment; intent still requires maintainer review."], downgraded_reason: null }];
    r.evidence = [{ ...r.evidence[0], id: "E-execution", tier: "E1", path: null, permalink: null, kind: "reproduction", result: "Synthetic preview: both sandbox runs raised the expected KeyError.", excerpt: "Run 1: KeyError: 'col'\nRun 2: KeyError: 'col'" }];
    r.not_checked = ["Other operating systems and dependency versions were not tested."];
    r.suggested_label = { label: "confirmed", basis: "evidence", reasons: ["the reported behaviour was reproduced"] };
  }
  return r;
}
