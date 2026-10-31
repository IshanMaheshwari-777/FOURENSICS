"""Golden regression: the end-to-end behaviour of every scenario is pinned in tests/golden/.

Any change to verdicts, evidence types, summaries, stages or code-version choice fails here
with a readable diff. If the change is intended, accept it with:

    UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_regression_golden.py

and review the golden file's diff before keeping it.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import pytest

from scenarios import SCENARIOS, digest, run_scenario

GOLDEN = Path(__file__).parent / "golden" / "pipeline_scenarios.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_pipeline_output_matches_golden(name, make_deps, fake_github, golden):
    actual = digest(run_scenario(make_deps, fake_github, name).result)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        data = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        data[name] = actual
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return
    assert name in golden, f"No golden output for {name!r}; run with UPDATE_GOLDEN=1 and review it."
    expected = golden[name]
    if actual != expected:
        diff = "\n".join(difflib.unified_diff(
            json.dumps(expected, indent=2, sort_keys=True).splitlines(),
            json.dumps(actual, indent=2, sort_keys=True).splitlines(),
            "golden", "actual", lineterm=""))
        pytest.fail(f"Pipeline behaviour changed for {name!r}. If intended, run with UPDATE_GOLDEN=1.\n{diff}")


def test_golden_covers_every_scenario(golden):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        pytest.skip("updating")
    assert set(golden) == set(SCENARIOS), "Golden file and scenario list are out of sync"
