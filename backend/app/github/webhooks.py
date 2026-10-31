"""Webhook signature check and event routing. Read-only: events only start analyses."""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from ..schemas import Target

HANDLED = {
    "issues": {"opened", "edited", "reopened"},
    "pull_request": {"opened", "edited", "reopened", "synchronize"},
}


def verify_signature(secret: str, body: bytes, header: Optional[str]) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def target_from_event(event: str, payload: dict) -> Optional[tuple[Target, Optional[int]]]:
    """Returns (target, installation_id) for events we analyse, else None."""
    action = payload.get("action")
    if action not in HANDLED.get(event, set()):
        return None
    repo = payload.get("repository") or {}
    owner, name = (repo.get("full_name") or "/").split("/", 1)
    installation_id = (payload.get("installation") or {}).get("id")
    if event == "issues":
        issue = payload.get("issue") or {}
        if "pull_request" in issue:
            return None
        number, url, kind = issue.get("number"), issue.get("html_url"), "issue"
    else:
        pr = payload.get("pull_request") or {}
        number, url, kind = pr.get("number"), pr.get("html_url"), "pr"
    if not (owner and name and number and url):
        return None
    return Target(owner=owner, repo=name, kind=kind, number=int(number), url=url), installation_id
