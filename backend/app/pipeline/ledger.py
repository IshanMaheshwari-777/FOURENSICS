"""The evidence ledger: the single record of what the system actually observed.

The model may only cite ledger IDs. Paths, lines, excerpts and permalinks always
come from here, never from model output.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from ..github.urls import permalink
from ..schemas import EvidenceItem, Tier

MAX_EXCERPT_LINES = 60


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Ledger:
    def __init__(self, owner: str, repo: str):
        self.owner = owner
        self.repo = repo
        self.items: list[EvidenceItem] = []
        self.roots: dict[str, Path] = {}
        self._dedupe: dict[tuple, EvidenceItem] = {}

    def register_root(self, sha: str, root: Path) -> None:
        self.roots[sha] = Path(root)

    def _read(self, sha: str, path: str, start: int, end: int) -> Optional[str]:
        root = self.roots.get(sha)
        if root is None:
            return None
        try:
            source = (root / path).resolve()
            if not source.is_relative_to(root.resolve()):
                return None
            lines = source.read_text(errors="ignore").splitlines()
        except OSError:
            return None
        if start < 1 or start > len(lines):
            return None
        end = max(start, min(end, len(lines)))
        return "\n".join(lines[start - 1:end])

    def add(self, kind: str, tier: Tier, result: str, sha: str, claim_ids: Optional[list[str]] = None,
            path: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None,
            raw: str = "", absence: bool = False) -> EvidenceItem:
        key = (kind, sha, path, start, end, result)
        if key in self._dedupe:
            item = self._dedupe[key]
            if tier < item.tier:  # "E2" < "E3": keep the strongest tier any probe assigned
                item.tier = tier
            for c in claim_ids or []:
                if c not in item.claim_ids:
                    item.claim_ids.append(c)
            return item

        excerpt = content_hash = link = None
        if path and start:
            end = end or start
            if end - start + 1 > MAX_EXCERPT_LINES:
                end = start + MAX_EXCERPT_LINES - 1
            excerpt = self._read(sha, path, start, end)
            if excerpt is not None:
                content_hash = _hash(excerpt)
                end = start + excerpt.count("\n")
            link = permalink(self.owner, self.repo, sha, path, start, end)
        elif path:
            link = permalink(self.owner, self.repo, sha, path)

        item = EvidenceItem(
            id=f"E{len(self.items) + 1:03d}", claim_ids=list(claim_ids or []), kind=kind, tier=tier, sha=sha,
            path=path, start=start, end=end if start else None, excerpt=excerpt, content_hash=content_hash,
            result=result, raw=raw[:2000], permalink=link, absence=absence,
        )
        self.items.append(item)
        self._dedupe[key] = item
        return item

    def link(self, item_id: str, claim_id: str) -> None:
        for it in self.items:
            if it.id == item_id and claim_id not in it.claim_ids:
                it.claim_ids.append(claim_id)

    def get(self, item_id: str) -> Optional[EvidenceItem]:
        return next((i for i in self.items if i.id == item_id), None)

    def for_claim(self, claim_id: str) -> list[EvidenceItem]:
        return [i for i in self.items if claim_id in i.claim_ids]

    def verify(self, item: EvidenceItem) -> bool:
        """Re-read the source and confirm the excerpt still matches its hash."""
        if not item.content_hash:
            return True
        current = self._read(item.sha, item.path or "", item.start or 0, item.end or 0)
        return current is not None and _hash(current) == item.content_hash
