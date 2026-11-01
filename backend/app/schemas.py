"""Shared contracts. Every pipeline stage talks only through these types.

The extension mirrors PanelResult in extension/src/types.ts, and
tests/test_contract.py checks extension/mock/panel_result.example.json
against it so the two sides cannot drift.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Kind = Literal["issue", "pr"]
Mode = Literal["live", "eval"]


class Target(BaseModel):
    owner: str
    repo: str
    kind: Kind
    number: int
    url: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class AuthorFollowup(BaseModel):
    created_at: datetime
    body: str


class PRContext(BaseModel):
    base_sha: str
    head_sha: str
    base_ref: str = ""
    linked_issues: list[int] = []


class AnalysisInput(BaseModel):
    """Everything the pipeline is allowed to see. Built only by firewall.py."""
    target: Target
    title: str
    body: str
    body_hash: str
    created_at: datetime
    default_branch: str = "main"
    repo_size_kb: int = 0  # from GitHub's repo metadata; 0 when unknown
    author_followups: list[AuthorFollowup] = []
    pr: Optional[PRContext] = None
    mode: Mode = "live"


SnapshotMethod = Literal["exact", "release", "date", "pr", "unknown"]


class Snapshot(BaseModel):
    sha: str = ""
    method: SnapshotMethod = "unknown"
    reliability: Literal["exact", "release-matched", "inferred", "unknown"] = "unknown"
    ref_label: str = ""
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None


AnchorKind = Literal["symbol", "path", "error", "traceback_frame", "version", "code_block", "commit", "route", "literal"]


class Anchor(BaseModel):
    kind: AnchorKind
    value: str
    line: Optional[int] = None  # traceback line number, when present
    func: Optional[str] = None  # traceback function name, when present
    context: Optional[str] = None  # for versions: the words just before, to tell whose version it is


ClaimType = Literal[
    "behavior", "implementation", "cause", "existence", "version", "docs",
    "environment", "performance", "security", "pr_fix", "pr_test", "pr_refactor",
]


class Claim(BaseModel):
    id: str
    text: str
    type: ClaimType
    is_primary: bool = False
    source_span: str
    inferred: bool = False
    anchors: list[Anchor] = []
    condition: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None


Tier = Literal["E1", "E2", "E3", "E4"]


class EvidenceItem(BaseModel):
    id: str
    claim_ids: list[str] = []
    kind: str
    tier: Tier
    sha: str
    path: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    excerpt: Optional[str] = None
    content_hash: Optional[str] = None
    result: str
    raw: str = ""
    permalink: Optional[str] = None
    absence: bool = False  # "we looked and found nothing": never proof of contradiction


Technical = Literal["supported", "contradicted", "inconclusive"]
Verification = Literal["reproduced", "not_reproduced", "not_attempted", "environment_unavailable"]
Relation = Literal["supports", "contradicts", "context"]
Strength = Literal["high", "medium", "low", "none"]


class ClaimAssessment(BaseModel):
    claim_id: str
    technical: Technical = "inconclusive"
    verification: Verification = "not_attempted"
    evidence_ids: list[str] = []
    relations: dict[str, Relation] = {}
    explanation: str = ""
    limitations: list[str] = []
    strength: Strength = "none"
    lean: Optional[Literal["supported", "contradicted"]] = None
    decided_by: Literal["rule", "model", "none"] = "none"
    downgraded_reason: Optional[str] = None
    # The model's explanation for a verdict the validator rejected. Shown only as unconfirmed.
    unconfirmed_reasoning: Optional[str] = None


StageStatus = Literal["ok", "degraded", "failed", "skipped"]


class StageResult(BaseModel):
    stage: str
    status: StageStatus
    reason: Optional[str] = None
    ms: int = 0


RunStatus = Literal["queued", "running", "partial", "complete", "error"]
Tone = Literal["supported", "contradicted", "mixed", "neutral", "not_defect", "pending"]
ReportKind = Literal["defect", "question", "feature", "docs", "other", "pr"]


class AnalysisBlocker(BaseModel):
    code: str
    stage: str
    reason: str
    next_step: str


class SuggestedLabel(BaseModel):
    """A triage hint. basis "evidence": settled by code or execution; "leaning": from report signals only."""
    label: Literal["confirmed", "invalid", "cannot_reproduce"]
    basis: Literal["evidence", "leaning"]
    reasons: list[str] = []


class PanelResult(BaseModel):
    run_id: str
    status: RunStatus = "queued"
    target: Optional[Target] = None
    report_kind: Optional[ReportKind] = None
    stages: list[StageResult] = []
    snapshot: Snapshot = Field(default_factory=Snapshot)
    summary_line: str = "Analysis queued"
    summary_tone: Tone = "pending"
    claims: list[Claim] = []
    assessments: list[ClaimAssessment] = []
    evidence: list[EvidenceItem] = []
    missing_info: list[str] = []
    report_signals: list[str] = []
    suggested_label: Optional[SuggestedLabel] = None
    reply_draft: Optional[str] = None
    not_checked: list[str] = []
    notes: list[str] = []
    blockers: list[AnalysisBlocker] = []
    pipeline_version: str = ""
    models: dict[str, str] = {}
    error: Optional[str] = None
