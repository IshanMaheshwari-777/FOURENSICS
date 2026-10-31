"""Claim extraction overlaps the code checkout and index; results stay identical."""
from __future__ import annotations

import threading
import time

from app.db import new_run_id
from app.llm.gateway import FakeGateway, LLMUnavailable
from app.pipeline.orchestrator import analyze
from app.schemas import PanelResult, Target

from conftest import OWNER, REPO, adjudicate_response, extract_response
from scenarios import digest

TARGET = Target(owner=OWNER, repo=REPO, kind="issue", number=42, url=f"https://github.com/{OWNER}/{REPO}/issues/42")
DELAY = 0.4


def _run(deps):
    result = PanelResult(run_id=new_run_id())
    deps.db.new_run(f"parallel:{result.run_id}", result)
    return analyze(deps, result, target=TARGET)


def test_extraction_runs_while_the_code_is_checked_out(make_deps):
    events: list[tuple[str, float]] = []
    lock = threading.Lock()

    def note(name):
        with lock:
            events.append((name, time.monotonic()))

    def slow_extract(ctx):
        note("extract-start")
        time.sleep(DELAY)
        note("extract-end")
        return extract_response(ctx)

    gw = FakeGateway({"extract": slow_extract, "adjudicate": adjudicate_response})
    deps = make_deps(gateway=gw)
    real_worktree = deps.repos.worktree

    def slow_worktree(*args, **kwargs):
        note("checkout-start")
        time.sleep(DELAY)
        out = real_worktree(*args, **kwargs)
        note("checkout-end")
        return out

    deps.repos.worktree = slow_worktree
    t0 = time.monotonic()
    result = _run(deps)
    elapsed = time.monotonic() - t0

    at = dict(events)
    assert at["extract-start"] < at["checkout-end"], "extraction waited for the checkout"
    assert at["checkout-start"] < at["extract-end"], "the two steps did not overlap"
    assert elapsed < 2 * DELAY + 1.0, f"no time saved: {elapsed:.2f}s"
    assert result.status == "complete" and [c.id for c in result.claims] == ["C1", "C2", "C3"]
    assert [c["purpose"] for c in gw.calls].count("extract") == 1, "extraction must still run exactly once"


def test_same_result_as_before(make_deps):
    """The golden snapshot pins the full output; this checks the parallel path end to end once more."""
    from test_regression_golden import GOLDEN
    import json
    expected = json.loads(GOLDEN.read_text())["bug_with_wrong_explanation"]
    assert digest(_run(make_deps())) == expected


def test_extraction_failure_degrades_exactly_as_before(make_deps):
    gw = FakeGateway({"extract": LLMUnavailable("down"), "adjudicate": adjudicate_response})
    result = _run(make_deps(gateway=gw))
    stage = next(s for s in result.stages if s.stage == "claims")
    assert stage.status == "degraded" and "down" in stage.reason
    assert len(result.claims) == 1 and result.claims[0].is_primary  # title fallback, as before
