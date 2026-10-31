# FOURENSICS — Hacktoberfest 2026 Hack Day Sonipat

FOURENSICS checks the technical claims in public GitHub issues and pull requests. Maintainers receive structured findings backed by source excerpts, commit permalinks, deterministic checks, and explicitly scoped reproduction results. It does not try to identify whether a contributor used AI.

| Field | Actual implementation |
| --- | --- |
| Problem | Problem 02: Evidence-Based Open Source Issue and Pull Request Triage |
| Team | Buy2Get1 — Ishan Maheshwari, Ganga Raghuwanshi, Anuradha Raghuwanshi, Bhawana |
| Frontend | React + TypeScript; Manifest V3 extension with a Shadow DOM panel |
| Backend | FastAPI + Python; fixed verification pipeline; SQLite; Git snapshot cache |
| Model integrated | Hosted open-weight `gemma-4-26b-a4b-it` through Google's native API; Gemma 4 31B and optional OpenAI-compatible GPT-OSS 20B also supported |
| Provider selection | `GOOGLE_API_KEY` selects Gemma automatically; `LLM_PROVIDER` can explicitly select Google or the OpenAI-compatible provider |
| Retrieval integrated | Python AST, BM25, symbol/path/error search, bounded exact literal search |
| Other models | Qwen embeddings and reranking are **not integrated**; optional future work only |
| License | MIT; see [LICENSE](LICENSE). Third-party models and repositories retain their own licenses. |
| Repository | https://github.com/IshanMaheshwari-777/FOURENSICS |
| Status | Working local implementation |

## Maintainer output

Each claim has a technical assessment (**supported**, **contradicted**, **inconclusive**) and a separate execution status (**reproduced**, **not reproduced**, **not attempted**, **environment unavailable**). Findings include evidence IDs, checked-commit links, code-version reliability, limitations, and missing information. The extension can prepare a reply draft, but never submits it.

The panel presents the main finding first, followed by outcome filters and individual claim cards. Each expanded claim separates its explanation, execution status, cited source facts or context, expandable excerpts, and limitations. Incomplete runs show blockers and next steps above the claims. Full stage results, scope, model, pipeline and commit provenance are available under analysis details. Light/dark styles and narrow layouts support long report text and source paths. These presentation rules do not depend on repository names or languages; semantic depth and reproduction coverage still vary by language and environment.

A false explanation does not automatically invalidate the reported behavior. Finding quoted text in source does not prove a bug. Finding no evidence does not prove a claim false. Reproducing an exception establishes the observation in the tested environment; it does not establish that the behavior is unintended.

## Architecture actually built

The model performs two bounded tasks: extracting claims and interpreting collected evidence. It does not select commands or execute code.

1. **Public input and firewall.** Check repository visibility before fetching issue/PR content. Copy the title, body, date, and—in live mode—the reporter's own follow-ups. Exclude labels, maintainer conclusions, reviews, and merge state.
2. **References and historical snapshot.** Extract symbols, files, stack frames, versions, errors, routes, quotes, logs, and source annotations. Prefer an appropriate named commit/release/branch; otherwise inspect the default-branch state preceding the report. Evaluation rejects newer snapshots.
3. **Deterministic evidence.** Python AST checks examine signatures, calls, routes, guards, and lookups. Other languages receive text definitions/chunks, exact matches, docs, and PR diffs. Literal search scans once without requiring ripgrep, is capped at 32 queries, and prioritizes source matches. Observations enter the evidence ledger.
4. **Model extraction.** Gemma extracts atomic claims. Validate structured output and tie claims to actual report spans. Failed model calls produce visible degraded states and a title-based fallback.
5. **Claim checks and reproduction.** Gather implementation, related code, tests, manifests, and docs. Run only explicitly allowlisted snippets when sandbox execution is enabled.
6. **Assessment and validation.** Narrow deterministic rules run first. The selected model interprets remaining evidence. Validate citation existence, excerpt hashes, evidence direction, and actual execution status. Reference matches alone cannot settle behavioral or transformation claims. Several unsupported-verdict patterns are downgraded. A real citation cannot guarantee a correct interpretation.
7. **Display.** The extension polls the API and presents findings and uncertainty. The development preview explicitly distinguishes mock states, saved assessments, and new live analyses.

| Code | Responsibility |
| --- | --- |
| [`main.py`](backend/app/main.py) | API, cache invalidation, jobs, signed webhooks, feedback |
| [`firewall.py`](backend/app/pipeline/firewall.py) | Allowed model inputs and evaluation isolation |
| [`snapshot.py`](backend/app/pipeline/snapshot.py) | Historical versions and worktrees |
| [`index.py`](backend/app/pipeline/index.py), [`probes.py`](backend/app/pipeline/probes.py) | Source structure, retrieval, deterministic observations |
| [`ledger.py`](backend/app/pipeline/ledger.py) | Evidence IDs, excerpts, hashes, permalinks |
| [`adjudicate.py`](backend/app/pipeline/adjudicate.py), [`validator.py`](backend/app/pipeline/validator.py) | Rules, evidence interpretation, verdict safeguards |
| [`sandbox.py`](backend/app/pipeline/sandbox.py) | Restricted Python reproduction |
| [`gateway.py`](backend/app/llm/gateway.py) | Structured output, caching, pacing, retries, prompt limits |
| [`google.py`](backend/app/llm/google.py), [`factory.py`](backend/app/llm/factory.py) | Native Google Gemma transport, secret-header authentication and shared provider selection |
| [`Panel.tsx`](extension/src/panel/Panel.tsx) | Maintainer findings and uncertainty |

## Security and execution boundaries

The application reads public repository data only. It receives signed issue/PR events and supports short-lived GitHub App installation tokens. It does not publish comments, create issues, change labels, or write repository content.

Static inspection does not import or execute repository code. File readers prevent repository symlinks from reading host files outside the checkout, and Git metadata pointers are excluded from search. Submitted PR code is never executed by the analysis pipeline; CI results are reported as GitHub supplies them.

Sandbox execution is off by default and restricted to a local allowlist. One container installs a pinned public package with network access; package installation can itself execute package code, so package/version choices are reviewed. The selected reporter snippet then runs twice with no network, a read-only filesystem and mount, an unprivileged user, dropped capabilities, resource limits, and a timeout. This is a scoped Python demonstration, not a hardened execution service for arbitrary untrusted repositories.

Never publish `.env`, keys, local databases, or model-call logs. This is a local hackathon service; public internet deployment requires additional access controls and operational hardening.

## Real examples and evaluation

[`eval/examples/`](eval/examples/) contains actual observations. Synthetic mocks remain separately identified in [`extension/mock/`](extension/mock/).

- [`literal_checks.json`](eval/examples/literal_checks.json): historical Flask, Vite, Zod, and Cobra reports; Zod's `@__PURE__` annotation is found in source comments. These are retrieval checks, not correctness verdicts.
- [`reproduction.json`](eval/examples/reproduction.json): actual Docker observations for Flask issue 6177 and Pydantic issue 12072. Both reported exceptions occurred twice. Flask is checked on published 3.1.2, while its reporter used an unpinned development checkout; these are not identical environments.
- [`panel_saved_assessment.png`](eval/examples/panel_saved_assessment.png): a real saved assessment displayed in the panel, with source and pipeline version identified.
- [`panel_google_verified_pr.json`](eval/examples/panel_google_verified_pr.json) and [`panel_hierarchy_google_pr.png`](eval/examples/panel_hierarchy_google_pr.png): a read-only API retrieval of the existing pipeline 29 Gemma PR assessment, shown in the revised panel. Two claims are supported, three remain unresolved, and execution is explicitly not attempted. This display check starts no new analysis.
- [`panel_narrow_layout.png`](eval/examples/panel_narrow_layout.png): synthetic presentation fixtures at 360px (dark, long source text) and 320px (light, incomplete run). These are layout checks, not new verification results.
- [`model_assessment.json`](eval/examples/model_assessment.json): the complete saved GPT-OSS assessment behind that screenshot, from exploratory pipeline 16. It is a real model output, not a synthetic mock or a new evaluation of pipeline 21.
- [`panel_live_flask.json`](eval/examples/panel_live_flask.json) and [`panel_live_assessment.png`](eval/examples/panel_live_assessment.png): the browser-verified API-to-panel flow on pipeline 21. The isolated demo API on 8790 is cache-only; the normal backend on 8787 is unchanged. The model-extraction cache missed in both reproduction demos, so their title-based fallback is explicitly recorded as a degraded stage. The reproduction observations are real, deterministic Docker results.
- [`provenance_checks.json`](eval/examples/provenance_checks.json): source-probe/validator replay on saved Python, JavaScript, and Go reports/PRs. The unrelated-chart false confirmation is downgraded; this is not a fresh model assessment.
- [`api_checks.json`](eval/examples/api_checks.json): HTTP API checks of five user-supplied Zod, HTTPX, fzf and Alamofire URLs on pipeline 22. Cached extraction succeeds, while uncached adjudication is explicitly partial. No live inference was called. A rejected primary claim now falls back to the report title rather than promoting a debugging detail. This checks failure handling and retrieval, not verdict accuracy.

The frozen [public dataset](eval/data/public_20261101/) contains 33 unedited closed issues created on or after 2025-07-01. Questions and answers are separate. Collection is ordered rather than handpicked, excludes conflicting/edited reports, and uses seeded splits stratified by repository and reference label.

There are 3 `confirmed` issues from python-semantic-release, 15 `cannot reproduce` issues from marimo, and 15 `invalid` issues from LlamaFactory. **Repository and label are confounded**, and the confirmed class is small. Labels are unreviewed reference proxies: a confirmed documentation request is not necessarily a confirmed runtime defect. This sample cannot establish broad benchmark performance.

Reports compare strict predictions, forced-choice guesses, and GPT-OSS on issue text alone. Strict abstentions reduce coverage and overall accuracy. Forced choice includes heuristics and is not the verified product conclusion. Held-out cases are evaluated after behavior is frozen. Manifests record selected IDs, question-file hash, model, version, and settings. Evaluation uses a separate run database so live-server reloads cannot interrupt its records. No superiority over commercial tools is claimed.

The [dev report](eval/examples/report_dev.md) covers nine exploratory runs: strict coverage 3/9, with 1/3 answered predictions matching the reference labels (1/9 overall); forced choice matches 2/9. Only 3/9 text-only baseline answers are cached, so a fair baseline comparison is unavailable. A current-probe replay removes one unsupported confirmation, leaving 2/9 answered and 1/2 matching; this is a validation diagnostic, not final-pipeline accuracy. **These are weak results and do not demonstrate that the problem is solved.**

The daily 200,000-token limit stopped further inference. Per the team's decision, completion switched to cached results. The [held-out report](eval/examples/report_test.md) explicitly records that all 20 test questions remain unrun. Daily-quota detection now stops further live retries in the gateway; cached responses remain usable.

Final local validation (`scripts/dev.sh check`): secret scan, **199 backend tests passed** (2 opt-in Docker tests skipped), 13 panel checks, extension typecheck/build, and 7 bundle/manifest checks. An earlier run with Docker enabled is recorded in the [validation record](eval/examples/validation.json).

The API distinguishes incomplete model analysis from insufficient report information using `blockers` (stage, code, reason, next step). Degraded stages return `partial`; deterministic verdicts remain intact. Saved reports receive the same diagnostic explanation on read, without rerunning inference or rewriting their historical pipeline version. Model discovery being reachable does not establish available quota: health exposes inference availability separately.

Pipeline 23 adds an explicit `LLM_CACHE_ONLY` setting and a daily-quota cooldown using the provider's `Retry-After`, or a configurable one-hour fallback. Cooldown expiry permits the next requested attempt; it never starts background inference or guarantees the provider has restored quota. Health exposes the remaining cooldown. Changing model, endpoint or cache-only mode invalidates the run cache while preserving matching model-response cache entries. These recovery paths are tested against mocked providers.

Pipelines 24–25 integrate Google-hosted open-weight Gemma using `GOOGLE_API_KEY`. Native requests keep credentials out of URLs and prompts. Model output is parsed and validated locally, with one bounded JSON repair attempt; blocked or incomplete candidates are rejected. Only Gemma model IDs are accepted by the Google adapter. No proprietary Gemini fallback or paid-tier upgrade is performed. API, evaluation and demo entry points share the provider factory. The first public issue check successfully used Gemma for both extraction and evidence assessment; the main build-warning claim remained inconclusive because no build was executed. Availability is separate from correctness.

Pipelines 26–29 make bounded text retrieval deterministic across process restarts and keep ambiguous path matches weak. A reference-only verdict must describe bare presence; the model cannot bypass this safeguard by labeling a behavioral or compound claim as `existence`. A changed-test flag settles only an unqualified test-change claim; numerical counts and test outcomes need appropriate measured evidence. The free 31B endpoint returned HTTP 500s on two of five public checks. Both completed with 26B A4B, which became the default for this integration; this is an operational choice, not a demonstrated accuracy advantage. Google live checks and subsequent offline validator replay are archived separately in [`google_api_checks.json`](eval/examples/google_api_checks.json), preserving original run versions and model names. These checks do not establish accuracy on the held-out dataset or superiority over GPT-OSS.

## CI

The [CI workflow](.github/workflows/ci.yml) defines backend tests and extension typecheck/build jobs, with read-only permissions and no model keys. Normal tests use local fixtures and a fake model; Docker tests are opt-in.

## Team understanding and limits

Every member should understand why the model is not the source of truth, why missing evidence cannot refute a report, how snapshots avoid hindsight, what reproduction establishes, and when abstention is correct.

Evidence tiers are engineering categories, not calibrated probabilities: E1 is execution evidence; E2 is a deterministic structural fact; E3 is relevant source/documentation context; E4 is weak retrieval context. A caller or guard found only through a similarity match retains E4 relevance. The existence of that code is not evidence that the reported component uses it.

Python has the deepest analysis. Other languages receive shallower checks. Races, leaks, performance, OS behavior, services, and GPU workloads often remain unverified. The tool does not automatically reproduce arbitrary reports, inspect all dependency source, prove exploitability, or guarantee interpretation correctness. Rate limits and prompt budgets affect coverage; degraded stages remain visible.
