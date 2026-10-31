"""Deterministic validator. Plain code, so it cannot be talked out of its rules.

Any verdict that is not backed by real, matching evidence pointing in the right
direction is downgraded to inconclusive, with the reason recorded.
"""
from __future__ import annotations

import re

from ..schemas import Claim, ClaimAssessment, Snapshot, Verification
from .ledger import Ledger
from .claim_scope import bare_test_change, test_outcome_claim

DIRECTIONAL_TIERS = ("E1", "E2", "E3")
DOC_KINDS = {"doc_excerpt", "docstring", "symbol_in_docs_only", "literal_in_docs"}
NAME_ABSENCE_KINDS = {"symbol_absent", "path_absent"}
_NEGATION = re.compile(r"\b(no|not|without|never|unchanged|untouched|doesn'?t|does not|isn'?t|is not)\b", re.I)
# Reference checks: a repo-wide "not found" is a positive fact about the report.
REFERENCE_ABSENCE_KINDS = {"symbol_absent", "path_absent", "frame_func_mismatch", "frame_line_out_of_range",
                           "pr_no_tests_changed"}


def validate(assessments: list[ClaimAssessment], claims: list[Claim], ledger: Ledger, snap: Snapshot,
             repro: dict[str, Verification]) -> list[ClaimAssessment]:
    by_id = {c.id: c for c in claims}
    out: list[ClaimAssessment] = []
    for a in assessments:
        a = a.model_copy(deep=True)
        reasons: list[str] = []

        # 1. Citations must exist and still match their source. Evidence gathered for another claim
        #    in the same report is real, so it may be cited too.
        kept_ids, kept_rel = [], {}
        for eid in a.evidence_ids:
            item = ledger.get(eid)
            if item is None:
                reasons.append(f"cited {eid}, which does not exist")
                continue
            if not ledger.verify(item):
                reasons.append(f"{eid} no longer matches the source")
                continue
            kept_ids.append(eid)
            kept_rel[eid] = a.relations.get(eid, "context")
        a.evidence_ids, a.relations = kept_ids, kept_rel

        # 2. Verification status must match what actually ran.
        actual = repro.get(a.claim_id, "not_attempted")
        if a.verification != actual:
            reasons.append(f"verification corrected from {a.verification} to {actual}")
            a.verification = actual

        # 3. Direction: a verdict needs real evidence pointing its way.
        def directional(rel: str) -> list[str]:
            ok = []
            for eid in a.evidence_ids:
                item = ledger.get(eid)
                if a.relations.get(eid) != rel or item.tier not in DIRECTIONAL_TIERS:
                    continue
                if rel == "contradicts" and item.absence:
                    continue  # absence is never contradiction
                ok.append(eid)
            return ok

        def reject(reason: str) -> None:
            """The verdict did not survive: keep its direction as a lean, and its reasoning only as unconfirmed."""
            reasons.append(reason)
            a.lean = a.technical if a.technical in ("supported", "contradicted") else a.lean
            a.technical = "inconclusive"
            # The rejected verdict's limits belong to its argument, so they move with it.
            rejected = " ".join(filter(None, [a.explanation] + [f"(Noted limit: {l})" for l in a.limitations]))
            a.unconfirmed_reasoning = rejected or None
            a.limitations = []
            a.explanation = "The evidence found doesn't settle this claim either way."
            a.relations = {k: "context" for k in a.relations}

        if a.technical == "supported" and not directional("supports"):
            reject("no cited evidence supports the claim")
        elif a.technical == "contradicted":
            contra = directional("contradicts")
            claim = by_id.get(a.claim_id)
            only_absence_refs = contra and all(ledger.get(e).kind in REFERENCE_ABSENCE_KINDS for e in contra)
            if not contra:
                reject("no cited evidence contradicts the claim (absence alone does not count)")
            elif claim is not None and _NEGATION.search(claim.text) and \
                    all(ledger.get(e).kind == "pr_diff_misses_claim" for e in contra):
                # "…and no Usage.md change": the diff not touching it is what the claim says.
                reject("the claim says something was not changed; an untouched file agrees with it")
            elif claim is not None and claim.type not in ("existence", "pr_test") and \
                    all(ledger.get(e).kind in NAME_ABSENCE_KINDS for e in contra):
                # "X isn't in this repo" says nothing about behaviour: X may belong to another library
                # (asyncio's task.cancel) or the stdlib. It only settles claims that X exists here.
                reject("a name missing from the repository only settles claims that it exists here")
            elif only_absence_refs and claim is not None and claim.type not in ("existence", "pr_test") \
                    and not claim.is_primary:
                reject("only missing-reference evidence, which is not enough for this claim type")

        # 3b. Documentation describes intent, not what the code does. It may contradict a bug report
        #     ("documented as intended behaviour"), but it can't confirm a bug or settle claims about code.
        if a.technical in ("supported", "contradicted"):
            rel = "supports" if a.technical == "supported" else "contradicts"
            cited = [ledger.get(e) for e in directional(rel)]
            claim = by_id.get(a.claim_id)
            docs_only = cited and all(i.kind in DOC_KINDS for i in cited)
            intended = a.technical == "contradicted" and claim is not None and claim.type in ("behavior", "docs")
            docs_claim = claim is not None and claim.type == "docs"
            reference_only = cited and all(i.kind in ("literal_match", "literal_in_docs", "symbol_found",
                                                       "symbol_defined_text", "path_found") for i in cited)
            # A model can mislabel a compound behavior claim as "existence".
            # Reference-only proof is sufficient only for a bare presence assertion.
            bare_presence = claim is not None and claim.type == "existence" and \
                re.search(r"\b(?:exists?|defined|present|found)\b", claim.text, re.I) and \
                not re.search(r"\b(?:and|but|while|when|after|before|without|unless)\b", claim.text, re.I)
            if reference_only and claim is not None and claim.type != "docs" and not bare_presence:
                reject("reference matches establish source presence, not the claimed behavior or transformation")
            if docs_only and not intended and not docs_claim:
                reject("only documentation backs this verdict; documentation shows intent, not what the code does")

        # 4. Without a known code version, no strong conclusions (execution results excepted).
        claim = by_id.get(a.claim_id)
        if a.technical in ("supported", "contradicted") and a.decided_by == "rule" and \
                claim is not None and claim.type == "pr_test" and not bare_test_change(claim):
            reject("the test-change rule does not establish test counts, coverage or outcomes")
        if a.technical in ("supported", "contradicted") and claim is not None and claim.type == "pr_test" and \
                re.search(r"\b\d+\s+(?:unit\s+|regression\s+)?tests?\b", claim.text, re.I):
            relation = "supports" if a.technical == "supported" else "contradicts"
            measured_count = any(ledger.get(e).tier == "E1" or ledger.get(e).kind == "pr_test_count"
                                 for e in directional(relation))
            if not measured_count:
                reject("no measured test count verifies the numerical claim; test changes alone are insufficient")
        if a.technical in ("supported", "contradicted") and claim is not None and test_outcome_claim(claim):
            relation = "supports" if a.technical == "supported" else "contradicts"
            outcome = [ledger.get(e) for e in directional(relation)]
            execution = any(e.tier == "E1" for e in outcome)
            # GitHub CI status can settle a claim explicitly about CI, not named assertions.
            ci = bool(re.search(r"\bCI\b", claim.text)) and any(e.kind == "pr_ci_status" for e in outcome)
            if not execution and not ci:
                reject("test changes do not establish test outcomes; no execution evidence verifies this claim")

        if snap.reliability == "unknown" and a.technical != "inconclusive" and a.verification != "reproduced":
            reject("the code version could not be determined")

        # An undecided claim has no evidence "for" or "against" it, only context.
        if a.technical == "inconclusive":
            a.relations = {k: "context" for k in a.relations}

        if reasons:
            a.downgraded_reason = "; ".join(reasons)
        out.append(a)
    return out
