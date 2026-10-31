"""End-to-end pipeline tests on the fixture repo with scripted model responses."""
from __future__ import annotations

from app.db import new_run_id
from app.llm.gateway import FakeGateway, LLMUnavailable
from app.pipeline.aggregate import eval_label
from app.pipeline.firewall import build_issue_input
from app.pipeline.orchestrator import analyze, fetch_input
from app.schemas import PanelResult, Target

from conftest import OWNER, REPO


def _target(n: int, kind: str = "issue") -> Target:
    path = "pull" if kind == "pr" else "issues"
    return Target(owner=OWNER, repo=REPO, kind=kind, number=n, url=f"https://github.com/{OWNER}/{REPO}/{path}/{n}")


def _run(deps, n: int, kind: str = "issue", mode: str = "live") -> PanelResult:
    result = PanelResult(run_id=new_run_id())
    deps.db.new_run(f"test:{n}:{result.run_id}", result)
    return analyze(deps, result, target=_target(n, kind), mode=mode)


def _a(result: PanelResult, claim_id: str):
    return next(a for a in result.assessments if a.claim_id == claim_id)


def test_real_bug_with_wrong_explanation(make_deps, origin):
    deps = make_deps()
    r = _run(deps, 42)
    assert r.status == "complete", r.stages
    shas = origin[1]

    # Snapshot: the reporter's release, not today's fixed code.
    assert r.snapshot.method == "release" and r.snapshot.sha == shas["v2.3.1"] and r.snapshot.ref_label == "v2.3.1"

    # The model's invented claim was dropped by span validation.
    assert [c.id for c in r.claims] == ["C1", "C2", "C3"]
    assert any("could not tie" in n for n in r.notes)

    c1, c2, c3 = _a(r, "C1"), _a(r, "C2"), _a(r, "C3")
    assert c1.technical == "supported" and c1.verification == "not_attempted"
    assert "E999" not in c1.evidence_ids and "does not exist" in (c1.downgraded_reason or "")
    assert c2.technical == "contradicted"
    # Absence-only contradiction is rejected by the validator, and its reasoning is kept only as unconfirmed.
    assert c3.technical == "inconclusive" and c3.lean == "contradicted"
    assert c3.unconfirmed_reasoning == "The crash happens before validate() runs."
    assert "doesn't settle" in c3.explanation
    assert all(rel == "context" for rel in c3.relations.values())

    assert r.summary_tone == "mixed"
    assert r.missing_info == [] and r.reply_draft is None

    # Every evidence item with a location has a permalink at the snapshot SHA, and the traceback frame matches.
    for e in r.evidence:
        if e.path and e.start:
            assert e.permalink.startswith(f"https://github.com/{OWNER}/{REPO}/blob/{e.sha}/{e.path}#L{e.start}")
    assert any(e.kind == "frame_match" and e.path == "csvtool/parser.py" for e in r.evidence)
    assert eval_label(r) == "confirmed"


def test_no_reply_suggestion_when_main_claim_is_backed(make_deps, fake_github):
    body = fake_github.issues[42]["body"]
    fake_github.issues[42]["body"] = body.replace("/usr/lib/python3.11/site-packages/", "").replace("I expected", "I hoped")
    r = _run(make_deps(), 42)
    assert _a(r, "C1").technical == "supported" and _a(r, "C1").strength in ("high", "medium")
    assert r.missing_info == [] and r.reply_draft is None


def test_fabricated_references_are_contradicted(make_deps):
    r = _run(make_deps(), 43)
    assert r.status == "complete"
    kinds = {e.kind for e in r.evidence}
    assert {"symbol_absent", "path_absent", "frame_line_out_of_range"} <= kinds
    assert _a(r, "C1").technical == "contradicted" and _a(r, "C1").decided_by == "rule"
    assert _a(r, "C2").technical == "contradicted"
    assert r.summary_tone == "contradicted"
    assert eval_label(r) == "invalid"
    assert "the environment (runtime or language version, and operating system)" in r.missing_info
    assert r.reply_draft and "runtime or language version" in r.reply_draft


def test_question_is_not_judged_as_bug(make_deps):
    r = _run(make_deps(), 44)
    assert r.report_kind == "question" and r.summary_tone == "not_defect"
    assert r.missing_info == [] and r.reply_draft is None
    assert eval_label(r) == "invalid"


def test_pull_request_path(make_deps, origin):
    r = _run(make_deps(), 7, kind="pr")
    assert r.status == "complete", r.stages
    assert r.snapshot.method == "pr" and r.snapshot.head_sha == origin[1]["fix"]
    kinds = {e.kind for e in r.evidence}
    assert {"pr_changed_symbol", "pr_diff_touches_claim", "pr_tests_changed", "pr_ci_unavailable"} <= kinds
    assert _a(r, "C1").technical == "supported"
    assert _a(r, "C2").technical == "supported" and _a(r, "C2").decided_by == "rule"
    assert r.summary_tone == "supported"
    assert any("not run" in n for n in r.not_checked)


def test_issue_url_that_is_a_pr_is_rerouted(make_deps):
    r = _run(make_deps(), 7, kind="issue")
    assert r.target.kind == "pr" and r.snapshot.method == "pr"


def test_model_down_degrades_instead_of_crashing(make_deps):
    gw = FakeGateway({"extract": LLMUnavailable("down"), "adjudicate": LLMUnavailable("down")})
    r = _run(make_deps(gateway=gw), 42)
    assert r.status == "partial"
    claims_stage = next(s for s in r.stages if s.stage == "claims")
    assert claims_stage.status == "degraded"
    assert len(r.claims) == 1 and r.claims[0].is_primary
    assert all(a.technical == "inconclusive" for a in r.assessments)
    assert any(e.kind == "frame_match" for e in r.evidence)  # tool results still shown
    assert r.summary_tone == "neutral"
    assert "interpretation unavailable" in r.summary_line
    assert r.blockers and all(a.explanation for a in r.assessments)


def test_no_gateway_configured(make_deps):
    r = _run(make_deps(gateway=None), 42)
    assert r.status == "partial" and r.models["reasoning"] == "unavailable"


def test_date_snapshot_never_uses_later_fix(make_deps, origin, fake_github):
    fake_github.issues[42]["body"] = fake_github.issues[42]["body"].replace("version 2.3.1", "the latest version")
    r = _run(make_deps(), 42, mode="eval")
    assert r.snapshot.method == "date" and r.snapshot.reliability == "inferred"
    assert r.snapshot.sha == origin[1]["pre_issue"] != origin[1]["fix"]
    assert any("approximate" in n for n in r.not_checked)


def test_commit_named_in_report_is_used_exactly(make_deps, origin, fake_github):
    sha7 = origin[1]["pre_issue"][:7]
    body = fake_github.issues[42]["body"].replace("Using csvtool version 2.3.1,", f"On main at `{sha7}` (also deadbeef12),")
    fake_github.issues[42]["body"] = body
    r = _run(make_deps(), 42)
    assert r.snapshot.method == "exact" and r.snapshot.sha == origin[1]["pre_issue"]
    assert "named in the report" in r.snapshot.ref_label
    assert not any("approximate" in n for n in r.not_checked)


def test_commit_after_the_report_is_not_used(make_deps, origin, fake_github):
    fix7 = origin[1]["fix"][:7]  # created after the issue: must be ignored
    body = fake_github.issues[42]["body"].replace("Using csvtool version 2.3.1,", f"On main at `{fix7}`,")
    fake_github.issues[42]["body"] = body
    r = _run(make_deps(), 42)
    assert r.snapshot.method == "date" and r.snapshot.sha == origin[1]["pre_issue"]


def test_firewall_blocks_labels_and_maintainer_comments(make_deps, fake_github):
    deps = make_deps()
    target, inp = fetch_input(deps, _target(42), "live")
    dumped = inp.model_dump_json()
    assert "confirmed" not in dumped.lower().replace("unconfirmed", "")
    assert "SECRET-MAINTAINER-VERDICT" not in dumped
    assert [f.body for f in inp.author_followups] == ["Also happens on Python 3.12."]

    _, inp_eval = fetch_input(deps, _target(42), "eval")
    assert inp_eval.author_followups == []
    assert "comments:42" in fake_github.calls  # live mode only

    r = _run(deps, 42)
    for call in deps.db.llm_calls_for(r.run_id):
        assert "SECRET-MAINTAINER-VERDICT" not in call["prompt_text"]
        assert '"labels"' not in call["prompt_text"]


def test_edited_body_changes_cache_key(make_deps, fake_github):
    a = build_issue_input(_target(42), fake_github.issues[42], "main")
    edited = {**fake_github.issues[42], "body": fake_github.issues[42]["body"] + "\nEDIT: never mind"}
    b = build_issue_input(_target(42), edited, "main")
    assert a.body_hash != b.body_hash
