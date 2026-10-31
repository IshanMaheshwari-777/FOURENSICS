"""Saved real assessments must keep loading after schema or pipeline changes.

These files record what the system actually produced on public repositories (eval/examples)
and the extension's bundled example. A schema change that can no longer read them would
break the saved-run API, the panel, and the evidence in the submission.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline.diagnostics import explain_result
from app.schemas import PanelResult

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted((ROOT / "eval" / "examples").glob("*.json")) + [ROOT / "extension" / "mock" / "panel_result.example.json"]


def _panel_results(node, where: str):
    """Every object shaped like a saved assessment, wherever it is nested."""
    if isinstance(node, dict):
        if {"run_id", "stages", "snapshot", "claims", "assessments"} <= set(node):
            yield where, node
            return
        for k, v in node.items():
            yield from _panel_results(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _panel_results(v, f"{where}[{i}]")


SAVED = [(f"{p.name}{where[len(p.name):]}", obj)
         for p in SOURCES if p.exists() for where, obj in _panel_results(json.loads(p.read_text()), p.name)]


def test_saved_examples_exist():
    assert len(SAVED) >= 5, "expected saved real assessments in eval/examples"


@pytest.mark.parametrize("where,obj", SAVED, ids=[w for w, _ in SAVED])
def test_saved_assessment_still_loads_and_reads_back(where, obj):
    result = PanelResult.model_validate(obj)
    shown = explain_result(result)  # what GET /api/runs/{id} returns for a saved run
    assert shown.status in ("queued", "running", "partial", "complete", "error")
    claim_ids = {c.id for c in result.claims}
    assert {a.claim_id for a in result.assessments} <= claim_ids, f"{where}: assessment for an unknown claim"
    # Reading back must not rewrite history.
    assert shown.pipeline_version == result.pipeline_version and shown.claims == result.claims
