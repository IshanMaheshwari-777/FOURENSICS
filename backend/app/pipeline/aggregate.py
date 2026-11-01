"""Evidence strength, the panel's summary wording, and the evaluation label mapping.

Strength levels are engineering rules, not calibrated probabilities.
"""
from __future__ import annotations

from typing import Literal, Optional

from ..schemas import Claim, ClaimAssessment, PanelResult, ReportKind, Snapshot, Tone
from .ledger import Ledger

EvalLabel = Literal["confirmed", "invalid", "cannot_reproduce"]


def strength_for(a: ClaimAssessment, ledger: Ledger, snap: Snapshot) -> str:
    if a.technical == "inconclusive":
        return "none"
    rel = "supports" if a.technical == "supported" else "contradicts"
    tiers = [ledger.get(e).tier for e in a.evidence_ids if a.relations.get(e) == rel and ledger.get(e)]
    if "E1" in tiers or ("E2" in tiers and a.decided_by == "rule"):
        level = "high"
    elif "E2" in tiers or tiers.count("E3") >= 2:
        level = "medium"
    elif tiers:
        level = "low"
    else:
        level = "none"
    if snap.reliability == "inferred" and level == "high":
        level = "medium"
    return level


def apply_strength(assessments: list[ClaimAssessment], ledger: Ledger, snap: Snapshot) -> None:
    for a in assessments:
        a.strength = strength_for(a, ledger, snap)  # type: ignore[assignment]


def summarize(report_kind: Optional[ReportKind], claims: list[Claim], assessments: list[ClaimAssessment]
              ) -> tuple[str, Tone]:
    if report_kind in ("question", "feature", "docs"):
        noun = {"question": "a question", "feature": "a feature request", "docs": "a documentation request"}[report_kind]
        return f"Looks like {noun}, not a bug report.", "not_defect"
    if report_kind == "other" and not any(a.technical != "inconclusive" for a in assessments):
        return "Doesn't make a specific technical claim that can be checked.", "not_defect"
    by_id = {a.claim_id: a for a in assessments}
    primary = next((c for c in claims if c.is_primary), claims[0] if claims else None)
    if primary is None:
        return "No checkable claims found.", "neutral"
    p = by_id.get(primary.id)
    others = [by_id[c.id] for c in claims if c.id != primary.id and c.id in by_id]
    other_contra = any(o.technical == "contradicted" for o in others)
    other_support = any(o.technical == "supported" for o in others)
    is_pr = report_kind == "pr"

    if p and p.verification == "reproduced":
        if other_contra:
            return "Reported behaviour reproduced; part of the explanation conflicts with the code.", "mixed"
        return "Reported behaviour reproduced in the sandbox.", "supported"

    if p and p.verification == "not_reproduced" and p.technical != "supported":
        return "Didn't reproduce with the reporter's snippet.", "neutral"
    if p and p.technical == "supported" and p.strength in ("high", "medium"):
        if other_contra:
            return ("The main change checks out, but part of the description doesn't match the code. Review recommended."
                    if is_pr else "The main problem is backed by the code, but part of the explanation doesn't match. Review recommended."), "mixed"
        return ("The diff backs the main claim." if is_pr else "Backed by the code."), "supported"
    if p and p.technical == "contradicted":
        return ("The diff doesn't match the main claim." if is_pr else "The code disagrees with the main claim."), "contradicted"
    if (p and p.technical == "supported") or (other_support and other_contra):
        return "Partly backed by the code. Review recommended.", "mixed"
    if other_contra:
        return "Part of the report doesn't match the code; the main problem couldn't be verified.", "mixed"
    return "The available evidence does not settle the main claim.", "neutral"


def main_claim_backed(claims: list[Claim], assessments: list[ClaimAssessment]) -> bool:
    primary = next((c for c in claims if c.is_primary), None)
    a = next((x for x in assessments if primary and x.claim_id == primary.id), None)
    return bool(a and a.technical == "supported" and a.strength in ("high", "medium"))


def not_checked(snap: Snapshot, assessments: list[ClaimAssessment], is_pr: bool, sandbox_ran: bool) -> list[str]:
    out: list[str] = []
    if is_pr:
        out.append("PR code was not run; CI results are reported as GitHub shows them.")
    elif not sandbox_ran:
        out.append("The code was not run (static check only).")
    if snap.reliability == "inferred":
        out.append(f"The code version is approximate ({snap.ref_label}); the reporter may have used another release.")
    if snap.reliability == "unknown":
        out.append("The code version could not be determined.")
    return out


def eval_label(result: PanelResult, forced: bool = False) -> Optional[EvalLabel]:
    """Maps a result to the evaluation labels, using the same suggestion the panel shows.

    Strict mode answers only when code or execution evidence settled it; forced mode also takes leanings."""
    from .triage import suggest_label
    s = result.suggested_label or suggest_label(result.report_kind, result.claims, result.assessments,
                                                result.report_signals)
    if s is not None and (s.basis == "evidence" or forced):
        return s.label
    if not forced:
        return None
    # No evidence and no report signals at all (for example a result saved before signals existed).
    return "cannot_reproduce" if len(result.missing_info) >= 3 else "confirmed"
