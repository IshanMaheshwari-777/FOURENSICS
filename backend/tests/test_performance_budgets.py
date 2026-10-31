"""Cost and speed budgets. They fail when a change makes analysis more expensive (extra model
or GitHub calls, larger prompts, evidence bloat) or much slower (e.g. an accidental quadratic).

Measured on the reference Mac when added: fixture runs ~0.25 s, 2 model calls, <=3 GitHub calls;
a 1,500-Python/800-JS/100-doc repo indexes in ~0.4 s and probes in ~1 s. Time budgets are about
10-25x that so slower CI machines pass, while real regressions do not.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.db import new_run_id
from app.llm.gateway import FakeGateway
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.index import build_index, get_index
from app.pipeline.ledger import Ledger
from app.pipeline.orchestrator import analyze
from app.pipeline.probes import claim_probes, reference_probes
from app.schemas import Claim, PanelResult, Snapshot, Target

from conftest import OWNER, REPO, adjudicate_response, extract_response
from scenarios import SCENARIOS, run_scenario

MAX_MODEL_CALLS_PER_RUN = 2        # one claim extraction + one evidence interpretation
MAX_GITHUB_CALLS_PER_RUN = 4       # repository, issue/PR, reporter follow-ups, CI checks
MAX_EVIDENCE_PER_RUN = 60          # more evidence than this crowds the model prompt
MAX_SECONDS_PER_FIXTURE_RUN = 10.0
DEFAULT_PROMPT_CHARS = 12000 * 4   # the default prompt budget, in characters

LARGE_INDEX_SECONDS = 15.0
LARGE_REFERENCE_SECONDS = 10.0
LARGE_CLAIM_PROBE_SECONDS = 15.0
MAX_EVIDENCE_PER_CLAIM = 25


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_each_run_stays_within_call_size_and_time_budgets(name, make_deps, fake_github):
    run = run_scenario(make_deps, fake_github, name)
    purposes = [c["purpose"] for c in run.model_calls]
    assert len(purposes) <= MAX_MODEL_CALLS_PER_RUN, f"extra model calls: {purposes}"
    assert purposes.count("adjudicate") <= 1 and purposes.count("extract") + purposes.count("extract_pr") <= 1
    assert len(run.github_calls) <= MAX_GITHUB_CALLS_PER_RUN, f"extra GitHub calls: {run.github_calls}"
    assert len(run.result.evidence) <= MAX_EVIDENCE_PER_RUN, f"evidence bloat: {len(run.result.evidence)} items"
    for call in run.model_calls:
        assert len(call["user"]) <= DEFAULT_PROMPT_CHARS, f"{call['purpose']} prompt exceeds the budget"
    assert run.seconds <= MAX_SECONDS_PER_FIXTURE_RUN, f"{name} took {run.seconds:.1f}s"


def test_a_small_model_limit_shrinks_prompts(make_deps, fake_github):
    """Servers with small limits (e.g. 8K tokens/minute) must get proportionally small prompts."""
    budget = 2500
    gw = FakeGateway({"extract": extract_response, "extract_pr": extract_response,
                      "adjudicate": adjudicate_response}, prompt_budget_tokens=budget)
    deps = make_deps(gateway=gw)
    result = PanelResult(run_id=new_run_id())
    deps.db.new_run("budget", result)
    analyze(deps, result, target=Target(owner=OWNER, repo=REPO, kind="issue", number=42,
                                       url=f"https://github.com/{OWNER}/{REPO}/issues/42"))
    adjudications = [c for c in gw.calls if c["purpose"] == "adjudicate"]
    assert adjudications, "adjudication did not run"
    for c in adjudications:
        assert len(c["user"]) <= budget * 4, f"prompt of {len(c['user'])} chars ignores a {budget}-token budget"


@pytest.fixture(scope="module")
def large_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("large_repo")
    for i in range(1500):
        d = root / "pkg" / f"mod{i // 100}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"m{i}.py").write_text("\n".join(
            f"def func_{i}_{j}(a, b=None):\n    if a is None:\n        return b\n    return helper_{j}(a)[0]\n"
            for j in range(8)))
    for i in range(800):
        d = root / "web" / f"part{i // 100}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"c{i}.js").write_text("\n".join(
            f"export function render{i}_{j}(x) {{ return x.map(v => v + {j}); }}" for j in range(30)))
    (root / "docs").mkdir()
    for i in range(100):
        (root / "docs" / f"guide{i}.md").write_text(f"# Guide {i}\n\nCall `func_{i}_1` to parse rows.\n\n" * 5)
    return root


LARGE_REPORT = (
    "`func_12_3()` in pkg/mod0/m12.py crashes with `KeyError: 'col'` when called with None.\n"
    "```\nTraceback:\n  File \"pkg/mod0/m12.py\", line 14, in func_12_3\nKeyError: 'col'\n```\n"
    "Also `render5_2` in web/part0/c5.js, `missing_function()`, and the log line 'Rows are dropped'. version 1.2.3")


def test_large_repository_indexing_and_probing_stay_fast(large_repo):
    t0 = time.monotonic()
    idx = build_index(large_repo)
    index_s = time.monotonic() - t0
    assert len(idx.symbols) >= 12000 and idx.code_chunks and idx.docs
    assert index_s <= LARGE_INDEX_SECONDS, f"indexing took {index_s:.1f}s"

    anchors = extract_anchors(LARGE_REPORT)
    ledger = Ledger("o", "r")
    ledger.register_root("s", large_repo)
    t0 = time.monotonic()
    reference_probes(idx, anchors, Snapshot(sha="s"), ledger)
    ref_s = time.monotonic() - t0
    assert ref_s <= LARGE_REFERENCE_SECONDS, f"reference checks took {ref_s:.1f}s"

    claims = [Claim(id=f"C{k}", text=text, type="behavior", is_primary=k == 1, source_span="x", anchors=anchors)
              for k, text in enumerate(["func_12_3 crashes with KeyError when called with None",
                                        "render5_2 maps values", "rows are dropped"], 1)]
    t0 = time.monotonic()
    for claim in claims:
        claim_probes(idx, claim, Snapshot(sha="s"), ledger)
    probe_s = time.monotonic() - t0
    assert probe_s <= LARGE_CLAIM_PROBE_SECONDS, f"claim checks took {probe_s:.1f}s"
    for claim in claims:
        n = len(ledger.for_claim(claim.id))
        assert n <= MAX_EVIDENCE_PER_CLAIM, f"{claim.id} collected {n} evidence items"


def test_index_is_reused_not_rebuilt(large_repo):
    first = get_index(large_repo, "perf-test@large")
    t0 = time.monotonic()
    second = get_index(large_repo, "perf-test@large")
    assert second is first, "the index cache returned a new object"
    assert time.monotonic() - t0 < 0.05, "a cached index lookup should be instant"
