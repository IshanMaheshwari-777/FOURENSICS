"""Explain incomplete analysis without confusing a service failure with a bad report.

Also applied to saved API responses: no new inference, verdict changes or DB migration.
"""
from __future__ import annotations

from ..schemas import AnalysisBlocker, PanelResult


def explain_result(result: PanelResult) -> PanelResult:
    result = result.model_copy(deep=True)
    if result.status not in ("complete", "partial"):
        return result
    blockers: list[AnalysisBlocker] = []
    for stage in result.stages:
        if stage.status == "ok" or (stage.stage == "reproduction" and stage.status == "skipped"):
            continue
        reason = stage.reason or "This stage did not finish."
        lower = reason.lower()
        if stage.stage in ("claims", "adjudication"):
            if any(s in lower for s in ("quota", "tokens per day", "daily cap")):
                code, message, action = (
                    "model_quota_exhausted", "The model's daily quota was exhausted.",
                    "Retry after the provider's quota resets, or configure an available open-weight model endpoint.")
            elif any(s in lower for s in ("cache", "cached")):
                code, message, action = (
                    "model_cache_miss", "No cached model response matched this request.",
                    "Use an available inference endpoint to assess this evidence; cache-only mode cannot create new responses.")
            else:
                code, message, action = (
                    "model_analysis_incomplete", reason,
                    "Check the model endpoint and the recorded stage failure before retrying.")
        else:
            code, message, action = (
                "stage_incomplete", reason,
                f"Resolve the {stage.stage} limitation before relying on a complete assessment.")
        blockers.append(AnalysisBlocker(code=code, stage=stage.stage, reason=message, next_step=action))
    result.blockers = blockers
    model_blockers = [b for b in blockers if b.stage in ("claims", "adjudication")]
    if blockers:
        result.status = "partial"

    adjudication_blocked = any(b.stage == "adjudication" for b in model_blockers)
    if adjudication_blocked:
        reason = next(b.reason for b in model_blockers if b.stage == "adjudication")
        for a in result.assessments:
            if a.decided_by != "none" or a.technical != "inconclusive":
                continue
            a.explanation = f"Not assessed: {reason} Retrieved source context is not a verified verdict."
            # Preserve useful tool context even when interpretation was unavailable.
            if not a.evidence_ids:
                items = sorted((e for e in result.evidence if a.claim_id in e.claim_ids),
                               key=lambda e: (e.tier, e.id))[:5]
                a.evidence_ids = [e.id for e in items]
                a.relations = {e.id: "context" for e in items}

    primary = next((c for c in result.claims if c.is_primary), None)
    assessment = next((a for a in result.assessments if primary and a.claim_id == primary.id), None)
    unresolved = assessment is None or assessment.technical == "inconclusive"
    if model_blockers and unresolved:
        quota = any(b.code == "model_quota_exhausted" for b in model_blockers)
        headline = "Analysis incomplete: model quota exhausted." if quota else "Analysis incomplete: model interpretation unavailable."
        if result.evidence:
            headline += f" {len(result.evidence)} evidence items collected; main claim unverified."
        # Keep a successful report-type classification, without implying technical verification.
        if result.report_kind in ("question", "feature", "docs") and not any(b.stage == "claims" for b in model_blockers):
            noun = {"question": "Question", "feature": "Feature request", "docs": "Documentation request"}[result.report_kind]
            headline = f"{noun}; technical verification is incomplete."
        result.summary_line, result.summary_tone = headline, "neutral"
    elif result.summary_line == "Not enough information to check.":
        result.summary_line = "The available evidence does not settle the main claim."
    return result
