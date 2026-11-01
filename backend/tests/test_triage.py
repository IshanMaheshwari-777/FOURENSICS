"""Suggested triage label: code evidence first, report signals only as a marked leaning.

Reports here are synthetic and name no real project: the rules must work on any repository."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.pipeline import missing_info, triage
from app.pipeline.aggregate import eval_label
from app.pipeline.extract_regex import extract_anchors
from app.schemas import Claim, ClaimAssessment, PanelResult


def signals(text: str) -> list[str]:
    anchors = extract_anchors(text)
    return triage.report_signals(text, anchors, missing_info.check_missing(text, anchors))


def claim() -> list[Claim]:
    return [Claim(id="C1", text="it fails", type="behavior", is_primary=True, source_span="it fails")]


def assess(**kw) -> list[ClaimAssessment]:
    return [ClaimAssessment(claim_id="C1", **kw)]


HARDWARE = ("Title: Output freezes\n\nOn my laptop with an NVIDIA GPU and the latest driver the preview "
            "freezes after export. Expected the preview to keep updating, instead it stops.")
INTERMITTENT = ("Title: Sync fails\n\nSync sometimes fails with a timeout when saving a large document. "
                "I expected it to save every time but it does not.")
EMPTY_TEMPLATE = ("Title: Button broken\n\n### Steps to reproduce\n\n_No response_\n\n### Expected behavior\n\n"
                  "The button should open the dialog but nothing happens when I click it on the settings page.")
WITH_STEPS = ("Title: Export drops rows\n\n1. Open a sheet with 3 rows\n2. Export to CSV\n3. Open the file\n\n"
              "Expected 3 rows, but the file has 2.")
THIRD_PARTY = ("Title: Login broken\n\nSince yesterday the external service changed its login page and "
               "now signing in through the integration fails every time with an error.")


@pytest.mark.parametrize("text, expected", [
    (HARDWARE, "environment_specific"),
    (INTERMITTENT, "intermittent"),
    (EMPTY_TEMPLATE, "no_repro_steps"),
    (WITH_STEPS, "repro_steps"),
    (THIRD_PARTY, "third_party"),
    ("Title: help\n\nnot working pls fix", "too_little_content"),
])
def test_report_signals(text, expected):
    assert expected in signals(text)


def test_template_heading_alone_is_not_a_reproduction():
    assert "repro_steps" not in signals(EMPTY_TEMPLATE)


def test_one_hardware_word_is_not_enough():
    assert "environment_specific" not in signals("Title: x\n\nThe chart is wrong on my phone when the legend is long.")


@pytest.mark.parametrize("kw, label, basis", [
    ({"technical": "contradicted"}, "invalid", "evidence"),
    ({"technical": "supported"}, "confirmed", "evidence"),
    ({"verification": "reproduced"}, "confirmed", "evidence"),
    ({"verification": "not_reproduced"}, "cannot_reproduce", "evidence"),
    ({"lean": "contradicted"}, "invalid", "leaning"),
])
def test_code_evidence_decides_first(kw, label, basis):
    s = triage.suggest_label("defect", claim(), assess(**kw), signals(HARDWARE))
    assert (s.label, s.basis) == (label, basis)


def test_non_defect_reports_are_invalid_and_prs_get_no_label():
    assert triage.suggest_label("feature", [], [], []).label == "invalid"
    assert triage.suggest_label("pr", claim(), assess(technical="supported"), []) is None


def test_a_crash_path_in_code_is_only_a_leaning_when_nobody_can_trigger_it():
    """A stack trace matching the code shows the path exists, not that maintainers can reproduce it."""
    s = triage.suggest_label("defect", claim(), assess(technical="supported"), ["crash_log_only", "no_repro_steps"])
    assert (s.label, s.basis) == ("confirmed", "leaning")


@pytest.mark.parametrize("text, label", [
    (HARDWARE, "cannot_reproduce"),
    (INTERMITTENT, "cannot_reproduce"),
    (THIRD_PARTY, "invalid"),
])
def test_leanings_from_report_signals(text, label):
    s = triage.suggest_label("defect", claim(), assess(), signals(text))
    assert (s.label, s.basis) == (label, "leaning") and s.reasons


def test_a_complete_report_alone_is_not_evidence_of_a_bug():
    assert triage.suggest_label("defect", claim(), assess(), signals(WITH_STEPS)) is None


def test_a_code_leaning_outweighs_reproduction_obstacles():
    s = triage.suggest_label("defect", claim(), assess(lean="supported"), signals(HARDWARE))
    assert (s.label, s.basis) == ("confirmed", "leaning")


def test_strict_evaluation_answers_only_with_evidence():
    r = PanelResult(run_id="r", report_kind="defect", claims=claim(), assessments=assess(),
                    report_signals=signals(HARDWARE))
    assert eval_label(r) is None and eval_label(r, forced=True) == "cannot_reproduce"
    r.assessments = assess(technical="supported")
    assert eval_label(r) == "confirmed"


def test_results_saved_before_signals_still_score():
    r = PanelResult.model_validate({"run_id": "old", "report_kind": "defect", "claims": [c.model_dump() for c in claim()],
                                    "assessments": [], "missing_info": ["a", "b", "c"]})
    assert r.suggested_label is None and eval_label(r) is None and eval_label(r, forced=True) == "cannot_reproduce"


def test_rules_name_no_project_repository_or_language():
    """Signals must generalise: no project names or programming languages in the rule source."""
    src = Path(triage.__file__).read_text().lower()
    for name in ("python", "javascript", "typescript", "rust", "golang", "kotlin", "java ", "android", "github",
                 "wled", "anki", "svt", "luanti", "mealie", "prism", "resume", "openlist", "ant-design", "marimo"):
        assert not re.search(rf"\b{re.escape(name.strip())}\b", src), name


def test_results_carry_evidence_labels_only():
    """Leanings scored at chance on held-out issues, so results and the panel never carry them."""
    leaning = triage.suggest_label("defect", claim(), assess(), signals(HARDWARE))
    evidence = triage.suggest_label("defect", claim(), assess(technical="contradicted"), [])
    assert leaning.basis == "leaning" and triage.shown_label(leaning) is None
    assert triage.shown_label(evidence) is evidence and triage.shown_label(None) is None


def test_forced_evaluation_still_uses_leanings_hidden_from_the_panel():
    signals_ = signals(INTERMITTENT)
    stored = triage.shown_label(triage.suggest_label("defect", claim(), assess(), signals_))
    r = PanelResult(run_id="r", report_kind="defect", claims=claim(), assessments=assess(),
                    report_signals=signals_, suggested_label=stored)
    assert r.suggested_label is None
    assert eval_label(r) is None and eval_label(r, forced=True) == "cannot_reproduce"


def test_pipeline_results_never_store_a_leaning(make_deps, fake_github):
    from scenarios import SCENARIOS, run_scenario
    for name in SCENARIOS:
        r = run_scenario(make_deps, fake_github, name).result
        assert r.suggested_label is None or r.suggested_label.basis == "evidence", name
