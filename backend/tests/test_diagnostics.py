"""Service failures, open claims and rule verdicts must remain distinguishable."""
from app.llm.gateway import FakeGateway
from app.pipeline.claims import extract_claims
from app.pipeline.diagnostics import explain_result
from app.schemas import AnalysisInput, Claim, ClaimAssessment, EvidenceItem, PanelResult, StageResult, Target


def blocked(report_kind="defect"):
    return PanelResult(
        run_id="saved", pipeline_version="21", status="complete", report_kind=report_kind,
        summary_line="Not enough information to check.",
        claims=[Claim(id="C1", type="behavior", text="Crashes", source_span="Crashes", is_primary=True)],
        assessments=[ClaimAssessment(claim_id="C1")],
        evidence=[EvidenceItem(id="E1", kind="code_excerpt", tier="E3", sha="abc", result="Context", claim_ids=["C1"])],
        stages=[StageResult(stage="adjudication", status="degraded", reason="AI interpretation unavailable (daily quota exhausted)")])


def test_saved_quota_failure_is_partial_without_changing_verdict_or_original():
    original = blocked()
    r = explain_result(original)
    assert r.status == "partial" and r.blockers[0].code == "model_quota_exhausted"
    assert "quota exhausted" in r.summary_line and "1 evidence items" in r.summary_line
    assert r.assessments[0].technical == "inconclusive"
    assert r.assessments[0].relations == {"E1": "context"}
    assert r.assessments[0].explanation.startswith("Not assessed:")
    assert r.pipeline_version == "21"  # does not pretend an old run was rerun
    assert original.status == "complete" and not original.blockers
    assert explain_result(r) == r


def test_feature_classification_does_not_imply_successful_verification():
    r = explain_result(blocked("feature"))
    assert r.status == "partial" and "Feature request" in r.summary_line
    assert explain_result(r) == r


def test_rule_verdict_survives_unavailable_secondary_interpretation():
    r = blocked()
    r.assessments[0] = ClaimAssessment(claim_id="C1", technical="supported", verification="reproduced", decided_by="rule")
    r.summary_line, r.summary_tone = "Reported behaviour reproduced in the sandbox.", "supported"
    result = explain_result(r)
    assert result.status == "partial" and result.summary_tone == "supported"
    assert result.assessments[0].technical == "supported"


def test_cache_miss_is_distinct_from_insufficient_report_information():
    r = blocked()
    r.stages[0].reason = "No matching cached response; cache-only inference enabled"
    result = explain_result(r)
    assert result.blockers[0].code == "model_cache_miss"
    assert not result.missing_info and result.reply_draft is None


def test_unsettled_static_analysis_is_complete_when_no_stage_failed():
    r = blocked()
    r.stages = [StageResult(stage="adjudication", status="ok"), StageResult(stage="reproduction", status="skipped")]
    r.assessments[0].decided_by = "model"
    result = explain_result(r)
    assert result.status == "complete" and not result.blockers
    assert result.summary_line == "The available evidence does not settle the main claim."


def test_rejected_primary_does_not_promote_a_debugging_detail():
    inp = AnalysisInput(target=Target(owner="a", repo="b", kind="issue", number=1, url="https://github.com/a/b/issues/1"),
                        title="Connection leak after cancellation", body="Workaround: await sleep(0)",
                        body_hash="h", created_at="2025-01-01T00:00:00Z")
    gw = FakeGateway({"extract": {"report_kind": "defect", "claims": [
        {"text": "Connections leak", "is_primary": True, "source_span": "Unquoted model paraphrase"},
        {"text": "Workaround needs sleep", "source_span": "await sleep(0)", "symbols": ["sleep"]}]}})
    _, claims, notes = extract_claims(inp, [], gw)
    assert [(c.id, c.is_primary) for c in claims] == [("C1", True), ("C2", False)]
    assert claims[0].text == inp.title
    assert any("primary claim failed source grounding" in n for n in notes)
