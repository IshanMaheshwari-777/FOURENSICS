"""Adjudication. Hard rules decide first and cannot be overridden; the model
(call 2) only interprets the claims that remain open."""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..llm.gateway import Gateway, LLMTooLarge, estimate_tokens, load_prompt
from ..schemas import Claim, ClaimAssessment, EvidenceItem, Snapshot, Verification
from .ledger import Ledger
from .claim_scope import bare_test_change

PROMPT_VERSION = "adjudicate_v1"
STRONG_SNAPSHOTS = ("exact", "release-matched")
MAX_ITEMS_IN_PROMPT = 40
MAX_EXCERPT_CHARS = 1800


class CitedEvidence(BaseModel):
    id: str
    relation: Literal["supports", "contradicts", "context"] = "context"


class ModelAssessment(BaseModel):
    claim_id: str
    technical: Literal["supported", "contradicted", "inconclusive"] = "inconclusive"
    evidence: list[CitedEvidence] = Field(default_factory=list)
    explanation: str = ""
    limitations: list[str] = Field(default_factory=list)
    lean: Optional[Literal["supported", "contradicted"]] = None


class AdjudicationOut(BaseModel):
    assessments: list[ModelAssessment] = Field(default_factory=list)


def _contains_any(handler: EvidenceItem, sites: list[EvidenceItem]) -> bool:
    """True if any site's line lies inside the handler function (full range kept in raw as 'start-end')."""
    try:
        start, end = (int(x) for x in handler.raw.split("-"))
    except ValueError:
        start, end = handler.start or 0, handler.end or 0
    return any(s.path == handler.path and s.start and start <= s.start <= end for s in sites)


def hard_rules(claims: list[Claim], ledger: Ledger, snap: Snapshot,
               repro: dict[str, Verification]) -> dict[str, ClaimAssessment]:
    decided: dict[str, ClaimAssessment] = {}
    strong = snap.reliability in STRONG_SNAPSHOTS
    for c in claims:
        items = ledger.for_claim(c.id)
        verification = repro.get(c.id, "not_attempted")

        # Sandbox reproduction of the reporter's own snippet.
        if verification == "reproduced" and c.type in ("behavior", "pr_fix") and c.is_primary:
            e1 = [i for i in items if i.tier == "E1"]
            decided[c.id] = ClaimAssessment(
                claim_id=c.id, technical="supported", verification="reproduced",
                evidence_ids=[i.id for i in e1], relations={i.id: "supports" for i in e1},
                explanation="Running the reporter's snippet in an isolated sandbox produced the reported behaviour.",
                decided_by="rule")
            continue

        # A TypeError about arguments, and a call site that really omits them, settle the main claim.
        # Other claims with the same error (for example a different endpoint) may reach the function by
        # another path, so the model judges those with this evidence instead.
        mismatches = [i for i in items if i.kind == "call_site_mismatch"]
        if mismatches and c.is_primary and c.type in ("behavior", "implementation", "cause") \
                and snap.reliability != "unknown":
            sig = [i for i in items if i.kind == "signature"]
            # Does the endpoint named in the claim lead to the function containing the faulty call?
            on_path = [r for r in items if r.kind == "route_handler" and _contains_any(r, mismatches)]
            cited = mismatches + sig + on_path
            decided[c.id] = ClaimAssessment(
                claim_id=c.id, technical="supported", verification=verification,
                evidence_ids=[i.id for i in cited], relations={i.id: "supports" for i in cited},
                explanation=("The endpoint's handler calls the function without arguments it requires, "
                             "which raises exactly this TypeError." if on_path else
                             "The code calls the function without arguments it requires, which raises exactly "
                             "this TypeError when that line runs."),
                limitations=(["Not run: the faulty call is in this endpoint's handler, but the request itself "
                              "was not executed."] if on_path else
                             ["Static check: confirms the faulty call exists, not that this request path reaches it."]),
                decided_by="rule")
            continue

        # Fabricated or wrong references at a version we trust.
        if strong:
            bad_frames = [i for i in items if i.kind in ("frame_func_mismatch", "frame_line_out_of_range")]
            good_frames = [i for i in items if i.kind == "frame_match"]
            if bad_frames and not good_frames and c.is_primary:
                decided[c.id] = ClaimAssessment(
                    claim_id=c.id, technical="contradicted", verification=verification,
                    evidence_ids=[i.id for i in bad_frames], relations={i.id: "contradicts" for i in bad_frames},
                    explanation="The traceback in the report does not match the code at the reporter's version.",
                    limitations=["The reporter may have used a modified or different version."], decided_by="rule")
                continue

        if c.type == "existence":
            absent = [i for i in items if i.kind in ("symbol_absent", "path_absent")]
            found = [i for i in items if i.kind in ("symbol_found", "path_found")]
            if absent and not found and snap.reliability != "unknown":
                decided[c.id] = ClaimAssessment(
                    claim_id=c.id, technical="contradicted" if strong else "inconclusive", verification=verification,
                    evidence_ids=[i.id for i in absent], relations={i.id: "contradicts" for i in absent},
                    explanation="The named code does not exist in the repository at this version.",
                    lean=None if strong else "contradicted",
                    limitations=[] if strong else ["The code version is approximate; it may have existed in another release."],
                    decided_by="rule")

        # PR test claims are settled by the diff itself.
        if bare_test_change(c):
            changed = [i for i in items if i.kind == "pr_tests_changed"]
            none = [i for i in items if i.kind == "pr_no_tests_changed"]
            if changed:
                decided[c.id] = ClaimAssessment(
                    claim_id=c.id, technical="supported", verification=verification,
                    evidence_ids=[i.id for i in changed], relations={i.id: "supports" for i in changed},
                    explanation="The diff adds or changes test code.", decided_by="rule")
            elif none:
                decided[c.id] = ClaimAssessment(
                    claim_id=c.id, technical="contradicted", verification=verification,
                    evidence_ids=[i.id for i in none], relations={i.id: "contradicts" for i in none},
                    explanation="The diff does not change any test files or test functions.", decided_by="rule")
    return decided


def _item_for_prompt(i: EvidenceItem) -> dict:
    d = {"id": i.id, "kind": i.kind, "tier": i.tier, "result": i.result, "claims": i.claim_ids}
    if i.tier == "E4":
        d["note"] = "Similarity-only context: relevance to the reported component/path is unverified; cannot settle the claim."
    if i.path:
        d["location"] = f"{i.path}" + (f":{i.start}-{i.end}" if i.start else "")
    if i.absence:
        d["note"] = "absence result: we looked and found nothing"
    if i.kind == "pr_diff" and i.raw:
        d["diff"] = i.raw  # lines starting with - were removed, + were added
    return d


def _priority(i: EvidenceItem, primary_id: Optional[str]) -> tuple:
    # Strongest evidence first, the main claim's evidence before the rest, "found nothing" results last.
    return (i.tier, 0 if primary_id in i.claim_ids else 1, 1 if i.absence else 0, i.id)


def build_adjudication_prompt(claims: list[Claim], items: list[EvidenceItem], snap: Snapshot,
                              repro: dict[str, Verification], limits: list[str],
                              budget_tokens: int = 12000) -> str:
    """Packs the most useful evidence into the token budget; excerpts are trimmed before items are dropped."""
    claim_rows = [{"id": c.id, "type": c.type, "primary": c.is_primary, "text": c.text,
                   "condition": c.condition, "expected": c.expected, "observed": c.observed,
                   "reproduction": repro.get(c.id, "not_attempted")} for c in claims]
    primary_id = next((c.id for c in claims if c.is_primary), None)
    ranked = sorted(items, key=lambda i: _priority(i, primary_id))[:MAX_ITEMS_IN_PROMPT]
    parts = [
        f"Code version checked: {snap.ref_label or 'unknown'} (commit {snap.sha[:12] or 'unknown'}, "
        f"reliability: {snap.reliability}).",
        "Known limitations: " + ("; ".join(limits) if limits else "none recorded"),
        *(["This is a pull request. Code excerpts show the code AFTER the PR's changes. `pr_diff` items show "
           "exactly what the PR removed (-) and added (+); judge the PR's claims about its change from those."]
          if snap.method == "pr" else []),
        "",
        "Claims to assess:",
        json.dumps(claim_rows, ensure_ascii=False, indent=1),
        "",
        "Evidence items (only these IDs may be cited):",
    ]
    remaining = budget_tokens * 4 - sum(len(p) + 1 for p in parts)
    omitted = 0
    for i in ranked:
        line = json.dumps(_item_for_prompt(i), ensure_ascii=False)
        if len(line) + 1 > remaining:
            omitted += 1
            continue
        parts.append(line)
        remaining -= len(line) + 1
        if i.excerpt:
            room = min(MAX_EXCERPT_CHARS, remaining - 60)
            if room >= 300:
                excerpt = i.excerpt if len(i.excerpt) <= room else i.excerpt[:room] + "\n[...trimmed]"
                block = f"<untrusted id={i.id}>\n{excerpt}\n</untrusted>"
                parts.append(block)
                remaining -= len(block) + 1
    omitted += max(0, len(items) - MAX_ITEMS_IN_PROMPT)
    if omitted:
        parts.append(f"({omitted} weaker evidence item(s) were left out to fit the size limit.)")
    return "\n".join(parts)


def adjudicate(claims: list[Claim], ledger: Ledger, snap: Snapshot, repro: dict[str, Verification],
               gateway: Optional[Gateway], limits: list[str], run_id: Optional[str] = None
               ) -> tuple[list[ClaimAssessment], list[str]]:
    """Returns (assessments, notes). Model failures leave open claims inconclusive."""
    notes: list[str] = []
    decided = hard_rules(claims, ledger, snap, repro)
    open_claims = [c for c in claims if c.id not in decided]
    model_results: dict[str, ModelAssessment] = {}

    if open_claims and gateway is not None:
        relevant = [i for i in ledger.items if not i.claim_ids or set(i.claim_ids) & {c.id for c in open_claims}]
        system = load_prompt("adjudicate_v1")

        def call() -> AdjudicationOut:
            budget = getattr(gateway, "prompt_budget_tokens", 12000) - estimate_tokens(system)
            user = build_adjudication_prompt(open_claims, relevant, snap, repro, limits, budget_tokens=budget)
            return gateway.complete_json(
                purpose="adjudicate", prompt_version=PROMPT_VERSION, system=system,
                user=user, out_model=AdjudicationOut, run_id=run_id,
                context={"claims": open_claims, "items": relevant, "snapshot": snap},
            )

        try:
            try:
                out = call()
            except LLMTooLarge:
                out = call()  # the gateway lowered its budget from the server's stated limit; retry once smaller
                notes.append("Evidence was trimmed to fit the model server's size limit.")
            model_results = {a.claim_id: a for a in out.assessments}
            if any(c.id not in model_results for c in open_claims):
                notes.append("AI interpretation unavailable for some claims: the model response omitted their assessments.")
        except Exception as e:  # LLMUnavailable, LLMBadOutput, anything else: degrade, don't crash
            notes.append(f"AI interpretation unavailable ({str(e)[:160] or e.__class__.__name__}); "
                         "showing tool results only.")
    elif open_claims:
        notes.append("AI interpretation unavailable; showing tool results only.")

    results: list[ClaimAssessment] = []
    for c in claims:
        if c.id in decided:
            results.append(decided[c.id])
            continue
        m = model_results.get(c.id)
        verification = repro.get(c.id, "not_attempted")
        if m is None:
            results.append(ClaimAssessment(claim_id=c.id, verification=verification,
                                           limitations=["Not interpreted by the model."], decided_by="none"))
            continue
        results.append(ClaimAssessment(
            claim_id=c.id, technical=m.technical, verification=verification,
            evidence_ids=[e.id for e in m.evidence], relations={e.id: e.relation for e in m.evidence},
            explanation=m.explanation.strip()[:600], limitations=[l[:200] for l in m.limitations[:4]],
            lean=m.lean, decided_by="model"))
    return results, notes
