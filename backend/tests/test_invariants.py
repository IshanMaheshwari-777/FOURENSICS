"""Rules every pipeline result must obey, whatever the scenario. A future change that breaks
evidence integrity, honesty about uncertainty, or the data firewall fails here."""
from __future__ import annotations

import hashlib

import pytest

from app.pipeline.claims import span_in_text
from app.pipeline.diagnostics import explain_result
from app.pipeline.validator import NAME_ABSENCE_KINDS, REFERENCE_ABSENCE_KINDS
from app.schemas import PanelResult

from conftest import BUG_BODY, FABRICATED_BODY, QUESTION_BODY, PR_BODY
from scenarios import SCENARIOS, run_scenario

REPORT_TEXT = {42: BUG_BODY, 43: FABRICATED_BODY, 44: QUESTION_BODY, 7: PR_BODY}
DIRECTIONAL_TIERS = {"E1", "E2", "E3"}
# Text that must never reach a model: labels, maintainer conclusions, merge state.
FORBIDDEN_IN_PROMPTS = ["SECRET-MAINTAINER-VERDICT", '"labels"', '"merged"', "fixed in v2.4.0"]


@pytest.fixture(params=list(SCENARIOS))
def run(request, make_deps, fake_github):
    return run_scenario(make_deps, fake_github, request.param)


def test_every_citation_exists_and_points_the_right_way(run):
    r = run.result
    ev = {e.id: e for e in r.evidence}
    claims = {c.id: c for c in r.claims}
    for a in r.assessments:
        assert a.claim_id in claims, f"assessment for unknown claim {a.claim_id}"
        assert set(a.relations) <= set(a.evidence_ids), "relations for uncited evidence"
        for eid in a.evidence_ids:
            assert eid in ev, f"{a.claim_id} cites missing evidence {eid}"
        if a.technical == "inconclusive":
            assert set(a.relations.values()) <= {"context"}, f"{a.claim_id}: undecided claim has a direction"
            continue
        direction = "supports" if a.technical == "supported" else "contradicts"
        backing = [ev[i] for i in a.evidence_ids if a.relations.get(i) == direction and ev[i].tier in DIRECTIONAL_TIERS]
        assert backing, f"{a.claim_id} is {a.technical} without directional evidence"
        if a.technical == "contradicted":
            usable = [e for e in backing if not e.absence or e.kind in REFERENCE_ABSENCE_KINDS]
            assert usable, f"{a.claim_id} contradicted only by 'found nothing' results"
            if claims[a.claim_id].type not in ("existence", "pr_test"):
                assert any(e.kind not in NAME_ABSENCE_KINDS for e in usable), \
                    f"{a.claim_id}: a missing name cannot contradict a {claims[a.claim_id].type} claim"


def test_evidence_is_traceable_to_the_checked_commit(run):
    r = run.result
    ids = [e.id for e in r.evidence]
    assert len(ids) == len(set(ids)), "duplicate evidence IDs"
    for e in r.evidence:
        if e.excerpt is not None:
            assert e.content_hash == hashlib.sha256(e.excerpt.encode()).hexdigest(), f"{e.id} excerpt/hash mismatch"
        if e.path and e.start and e.permalink:
            assert f"/blob/{e.sha}/" in e.permalink and e.permalink.split("#")[0].endswith(e.path), e.id
            assert f"#L{e.start}" in e.permalink, e.id
        assert not e.permalink or e.permalink.startswith("https://github.com/"), f"{e.id} unsafe link"


def test_claims_come_from_the_report(run):
    r = run.result
    number = r.target.number
    primaries = [c for c in r.claims if c.is_primary]
    assert len(primaries) == (1 if r.claims else 0), "exactly one main claim"
    text = f"{(r.target.url)}\n" + REPORT_TEXT[number]
    for c in r.claims:
        if not c.inferred and c.source_span:
            # Titles are also valid sources (fallback claims use the title).
            assert span_in_text(c.source_span, text) or c.source_span == c.text, \
                f"{c.id} source span not found in the report: {c.source_span!r}"


def test_execution_status_matches_what_ran(run):
    r = run.result
    sandbox = any(e.kind == "sandbox_run" for e in r.evidence)
    for a in r.assessments:
        if a.verification in ("reproduced", "not_reproduced"):
            assert sandbox, f"{a.claim_id} claims execution but nothing ran"
    if not sandbox:
        assert any("not run" in n.lower() for n in r.not_checked), "static-only runs must say so"


def test_results_round_trip_and_read_back(run):
    r = run.result
    again = PanelResult.model_validate_json(r.model_dump_json())
    assert again == r
    shown = explain_result(again)  # the API read path
    assert shown.status in ("complete", "partial")
    if any(s.status in ("degraded", "failed") for s in r.stages if s.stage != "reproduction"):
        assert shown.status == "partial" and shown.blockers, "degraded runs must be reported as partial"


def test_firewall_holds_for_every_prompt(run):
    for prompt in run.prompts:
        for forbidden in FORBIDDEN_IN_PROMPTS:
            assert forbidden not in prompt, f"{forbidden!r} reached a model prompt"


def test_uncertainty_is_never_hidden(run):
    r = run.result
    if r.status == "partial" or any(a.decided_by == "none" for a in r.assessments):
        assert r.summary_tone in ("neutral", "not_defect", "mixed"), "an incomplete run must not look decided"
    if r.summary_tone == "supported":
        primary = next(c for c in r.claims if c.is_primary)
        assert next(a for a in r.assessments if a.claim_id == primary.id).technical == "supported"
