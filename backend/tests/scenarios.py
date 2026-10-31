"""Representative end-to-end runs on the fixture repository, shared by the regression,
invariant and budget tests. Each scenario covers a different path through the pipeline."""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass

from app.db import new_run_id
from app.llm.gateway import FakeGateway, LLMUnavailable
from app.pipeline.orchestrator import analyze
from app.schemas import PanelResult, Target

from conftest import OWNER, REPO

# name -> (kind, number, model available)
SCENARIOS = {
    "bug_with_wrong_explanation": ("issue", 42, True),
    "fabricated_references": ("issue", 43, True),
    "question_not_a_bug": ("issue", 44, True),
    "pull_request": ("pr", 7, True),
    "model_unavailable": ("issue", 42, False),
}


@dataclass
class ScenarioRun:
    name: str
    result: PanelResult
    model_calls: list[dict]
    github_calls: list[str]
    seconds: float
    prompts: list[str]


def run_scenario(make_deps, fake_github, name: str) -> ScenarioRun:
    kind, number, model_ok = SCENARIOS[name]
    gateway = None if model_ok else FakeGateway({"extract": LLMUnavailable("down"),
                                                 "adjudicate": LLMUnavailable("down")})
    deps = make_deps() if model_ok else make_deps(gateway=gateway)
    path = "pull" if kind == "pr" else "issues"
    target = Target(owner=OWNER, repo=REPO, kind=kind, number=number,
                    url=f"https://github.com/{OWNER}/{REPO}/{path}/{number}")
    before = len(fake_github.calls)
    result = PanelResult(run_id=new_run_id())
    deps.db.new_run(f"scenario:{name}:{result.run_id}", result)
    t0 = time.monotonic()
    result = analyze(deps, result, target=target)
    seconds = time.monotonic() - t0
    calls = list(deps.gateway.calls) if deps.gateway is not None else []
    prompts = [c["prompt_text"] for c in deps.db.llm_calls_for(result.run_id)]
    return ScenarioRun(name, result, calls, fake_github.calls[before:], seconds, prompts)


_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")


def digest(r: PanelResult) -> dict:
    """The behaviour that matters, without run IDs, timings or commit hashes."""
    ev = {e.id: e for e in r.evidence}
    return {
        "status": r.status,
        "report_kind": r.report_kind,
        "summary_tone": r.summary_tone,
        "summary_line": r.summary_line,
        "snapshot": {"method": r.snapshot.method, "reliability": r.snapshot.reliability,
                     "ref_label": _HEX.sub("<sha>", r.snapshot.ref_label)},
        "stages": [f"{s.stage}:{s.status}" for s in r.stages],
        "blockers": sorted(b.code for b in r.blockers),
        "claims": [{"id": c.id, "type": c.type, "primary": c.is_primary, "text": c.text} for c in r.claims],
        "assessments": [{
            "claim": a.claim_id, "technical": a.technical, "verification": a.verification,
            "decided_by": a.decided_by, "strength": a.strength, "lean": a.lean,
            "cited": sorted(f"{ev[i].kind}:{a.relations.get(i, '?')}" for i in a.evidence_ids if i in ev),
            "downgraded": bool(a.downgraded_reason),
        } for a in r.assessments],
        "evidence_kinds": dict(sorted(Counter(e.kind for e in r.evidence).items())),
        "missing_info": r.missing_info,
        "has_reply_draft": r.reply_draft is not None,
        "not_checked": r.not_checked,
    }
