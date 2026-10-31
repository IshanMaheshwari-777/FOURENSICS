"""Evidence retrieval: exact search first, BM25 second, merged with reciprocal rank fusion."""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Claim
from .extract_regex import symbol_leaf
from .index import RepoIndex, Symbol
from .search import search_text

RRF_K = 60


@dataclass
class Candidate:
    symbol: Symbol
    score: float
    anchored: bool  # found through an exact name/string match, not similarity alone


def _key(s: Symbol) -> tuple:
    return (s.path, s.start, s.qualname)


def retrieve(index: RepoIndex, claim: Claim, k: int = 6) -> list[Candidate]:
    exact: list[Symbol] = []
    needles: list[str] = []
    for a in claim.anchors:
        if a.kind == "symbol":
            needles.append(symbol_leaf(a.value))
        elif a.kind == "error" and ":" in a.value:
            msg = a.value.split(":", 1)[1].strip().strip("'\"")
            if len(msg) >= 3:
                needles.append(msg)
    for needle in needles[:6]:
        for hit in search_text(index.root, needle, max_hits=15, globs=["*.py"]):
            sym = index.enclosing(hit.path, hit.line)
            if sym and sym not in exact:
                exact.append(sym)

    query = " ".join(filter(None, [claim.text, claim.condition, claim.observed, " ".join(needles)]))
    bm25 = [s for s, _ in index.search_code(query, k=15)]

    scores: dict[tuple, float] = {}
    syms: dict[tuple, Symbol] = {}
    anchored: set[tuple] = set()
    for ranked, is_exact in ((exact, True), (bm25, False)):
        for rank, s in enumerate(ranked):
            key = _key(s)
            syms[key] = s
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            if is_exact:
                anchored.add(key)
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    # Prefer non-test code; tests are collected separately by the test probe.
    ordered = [key for key in ordered if not syms[key].is_test] + [key for key in ordered if syms[key].is_test]
    return [Candidate(syms[key], scores[key], key in anchored) for key in ordered[:k]]
