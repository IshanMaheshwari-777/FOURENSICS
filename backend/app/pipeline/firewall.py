"""The data firewall. The only place that turns GitHub API objects into AnalysisInput.

Allowlist, not blocklist: labels, state, assignees, maintainer comments, reviews,
merge status and linked/fixing PRs are never copied, so no later stage can see them.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

from ..schemas import AnalysisInput, AuthorFollowup, Mode, PRContext, Target

_LINKED = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.I)
MAX_BODY_CHARS = 20_000


def body_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n\n{body}".encode()).hexdigest()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_issue_input(
    target: Target,
    issue: dict,
    default_branch: str,
    mode: Mode = "live",
    comments: Optional[list[dict]] = None,
) -> AnalysisInput:
    title = (issue.get("title") or "").strip()
    body = (issue.get("body") or "")[:MAX_BODY_CHARS]
    followups: list[AuthorFollowup] = []
    if mode == "live" and comments:
        author = (issue.get("user") or {}).get("login")
        for c in comments:
            # Only the reporter's own follow-ups; never maintainer or third-party comments.
            if author and (c.get("user") or {}).get("login") == author:
                followups.append(AuthorFollowup(created_at=_parse_dt(c["created_at"]),
                                                body=(c.get("body") or "")[:4000]))
    return AnalysisInput(
        target=target,
        title=title,
        body=body,
        body_hash=body_hash(title, body),
        created_at=_parse_dt(issue["created_at"]),
        default_branch=default_branch or "main",
        author_followups=followups[:5],
        mode=mode,
    )


def build_pr_input(target: Target, pull: dict, mode: Mode = "live") -> AnalysisInput:
    title = (pull.get("title") or "").strip()
    body = (pull.get("body") or "")[:MAX_BODY_CHARS]
    base = pull.get("base") or {}
    head = pull.get("head") or {}
    linked = sorted({int(n) for n in _LINKED.findall(f"{title}\n{body}")})
    return AnalysisInput(
        target=target,
        title=title,
        body=body,
        body_hash=body_hash(title, body) + ":" + (head.get("sha") or ""),
        created_at=_parse_dt(pull["created_at"]),
        default_branch=base.get("ref") or "main",
        pr=PRContext(base_sha=base.get("sha", ""), head_sha=head.get("sha", ""),
                     base_ref=base.get("ref", ""), linked_issues=linked),
        mode=mode,
    )


def model_visible_text(inp: AnalysisInput) -> str:
    """The exact report text a model may read."""
    parts = [f"Title: {inp.title}", "", inp.body]
    for f in inp.author_followups:
        parts += ["", f"Follow-up by the reporter ({f.created_at.date()}):", f.body]
    return "\n".join(parts)
