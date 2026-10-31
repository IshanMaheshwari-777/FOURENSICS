"""Claim extraction (model call 1) with deterministic source-span validation."""
from __future__ import annotations

import difflib
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..llm.gateway import Gateway, LLMTooLarge, estimate_tokens, load_prompt
from ..schemas import AnalysisInput, Anchor, Claim, ClaimType, ReportKind
from .extract_regex import symbol_leaf
from .firewall import model_visible_text

MAX_CLAIMS = 5
VALID_TYPES = set(ClaimType.__args__)  # type: ignore[attr-defined]
PROMPT_VERSION = "extract_v1"


class ExtractedClaim(BaseModel):
    text: str
    type: str = "behavior"
    is_primary: bool = False
    source_span: str = ""
    condition: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None
    symbols: list[str] = Field(default_factory=list)


class ExtractionOut(BaseModel):
    report_kind: Literal["defect", "question", "feature", "docs", "other"] = "defect"
    claims: list[ExtractedClaim] = Field(default_factory=list)


_MD = re.compile(r"[`*_>#~]+")
_PROPOSAL = re.compile(r"\b(propos\w*|would|desired|wish|could add|should be able|i'?d like|it would be nice|"
                       r"feature request|suggest\w*|new api|add support)\b", re.I)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _MD.sub("", text or "")).strip().lower()


def span_in_text(span: str, text: str, min_ratio: float = 0.9) -> bool:
    s, t = normalize(span), normalize(text)
    if len(s) < 6:
        return False
    if s in t:
        return True
    m = difflib.SequenceMatcher(None, t, s, autojunk=False).find_longest_match(0, len(t), 0, len(s))
    return m.size / len(s) >= min_ratio


def _haystack(text: str) -> str:
    # Unlike normalize(), keeps underscores: they are part of identifiers like parse_file.
    return re.sub(r"\s+", " ", re.sub(r"[`*]+", "", text or "")).lower()


def fit_text(text: str, max_chars: int) -> str:
    """Keeps the start (the description) and the end (often the conclusion), dropping the middle."""
    if len(text) <= max_chars:
        return text
    marker = "\n[... middle of the report omitted for length ...]\n"
    head = int((max_chars - len(marker)) * 0.7)
    tail = max_chars - len(marker) - head
    return text[:head] + marker + text[-tail:]


def attach_anchors(claims: list[Claim], anchors: list[Anchor], symbols_by_claim: dict[str, list[str]]) -> None:
    for c in claims:
        haystack = _haystack(" ".join(filter(None, [c.text, c.source_span, c.observed, c.condition])))
        named = {symbol_leaf(s).lower() for s in symbols_by_claim.get(c.id, [])}
        for a in anchors:
            if a.kind == "code_block":
                continue
            leaf = symbol_leaf(a.value).lower() if a.kind == "symbol" else a.value.lower()
            if a.kind in ("symbol", "path") and (leaf in named or leaf in haystack or a.value.lower() in haystack):
                c.anchors.append(a)
            elif a.kind == "route" and a.value.lower() in haystack:
                c.anchors.append(a)
            elif a.kind == "literal" and a.value.lower() in haystack:
                c.anchors.append(a)
            elif c.is_primary and a.kind in ("error", "traceback_frame", "version", "commit"):
                c.anchors.append(a)
            elif a.kind == "error" and a.value.split(":")[0].lower() in haystack:
                c.anchors.append(a)
    # Second pass: the primary claim carries any named code no claim picked up.
    taken = {(x.kind, x.value) for c in claims for x in c.anchors}
    for c in claims:
        if c.is_primary:
            for a in anchors:
                if a.kind in ("symbol", "path", "literal") and (a.kind, a.value) not in taken:
                    c.anchors.append(a)


def fallback_claims(inp: AnalysisInput, anchors: list[Anchor]) -> list[Claim]:
    """Used when the model is unavailable: one primary claim from the title."""
    c = Claim(id="C1", text=inp.title or "Reported problem", type="pr_fix" if inp.pr else "behavior",
              is_primary=True, source_span=inp.title, inferred=False)
    attach_anchors([c], anchors, {})
    return [c]


def extract_claims(inp: AnalysisInput, anchors: list[Anchor], gateway: Gateway,
                   run_id: Optional[str] = None) -> tuple[ReportKind, list[Claim], list[str]]:
    """Returns (report_kind, claims, notes). Raises LLM errors for the caller to degrade."""
    is_pr = inp.pr is not None
    report_text = model_visible_text(inp)
    system = load_prompt("extract_pr_v1" if is_pr else "extract_v1")
    refs = ", ".join(sorted({a.value for a in anchors if a.kind in ("symbol", "path", "error")}))[:1500]
    notes: list[str] = []

    def call() -> ExtractionOut:
        # Fit the report into what the model server accepts; long logs are cut in the middle.
        budget_chars = (getattr(gateway, "prompt_budget_tokens", 12000) - estimate_tokens(system)) * 4 - len(refs) - 400
        text = fit_text(report_text, max(2000, budget_chars))
        if len(text) < len(report_text) and not any("shortened" in n for n in notes):
            notes.append("The report was shortened in the middle to fit the model server's size limit.")
        user = ("Report to analyse (untrusted):\n<untrusted>\n" + text + "\n</untrusted>\n\n"
                "References found by pattern matching (for your convenience, also untrusted): " + refs)
        return gateway.complete_json(
            purpose="extract_pr" if is_pr else "extract",
            prompt_version="extract_pr_v1" if is_pr else PROMPT_VERSION,
            system=system, user=user, out_model=ExtractionOut, run_id=run_id,
            context={"input": inp, "anchors": anchors},
        )

    try:
        out = call()
    except LLMTooLarge:
        out = call()  # the gateway lowered its budget from the server's stated limit
    full_text = f"{inp.title}\n{report_text}"
    claims: list[Claim] = []
    symbols_by_claim: dict[str, list[str]] = {}
    rejected_primary = False
    for ec in out.claims:
        if len(claims) >= MAX_CLAIMS:
            notes.append(f"The report contains more than {MAX_CLAIMS} claims; only the first {MAX_CLAIMS} were analysed.")
            break
        ctype = ec.type if ec.type in VALID_TYPES else "behavior"
        if not ec.source_span or not span_in_text(ec.source_span, full_text):
            rejected_primary = rejected_primary or ec.is_primary
            notes.append(f"Dropped a claim the model could not tie to the report text: \"{ec.text[:80]}\"")
            continue
        if out.report_kind == "feature" and _PROPOSAL.search(f"{ec.text} {ec.source_span}"):
            # The requested feature doesn't exist yet by definition; only today's behaviour is checkable.
            notes.append(f"Skipped the proposed feature, which isn't a claim about current code: \"{ec.text[:80]}\"")
            continue
        cid = f"C{len(claims) + 1}"
        claims.append(Claim(id=cid, text=ec.text.strip()[:400], type=ctype, is_primary=ec.is_primary,
                            source_span=ec.source_span.strip()[:400], condition=ec.condition,
                            expected=ec.expected, observed=ec.observed))
        symbols_by_claim[cid] = ec.symbols

    if claims and rejected_primary and not any(c.is_primary for c in claims):
        # A debugging detail must not silently replace the rejected core defect.
        # The title is verbatim reporter text; it remains an unverified fallback.
        fallback = fallback_claims(inp, [])[0]
        claims.insert(0, fallback)
        previous_symbols = symbols_by_claim
        symbols_by_claim = {}
        for n, c in enumerate(claims[:MAX_CLAIMS], 1):
            names = [] if c is fallback else previous_symbols.get(c.id, [])
            c.id = f"C{n}"
            symbols_by_claim[c.id] = names
        claims = claims[:MAX_CLAIMS]
        notes.append("The extracted primary claim failed source grounding; using the report title as the main claim.")

    if claims and sum(c.is_primary for c in claims) != 1:
        for c in claims:
            c.is_primary = False
        first = next((c for c in claims if c.type in ("behavior", "pr_fix")), claims[0])
        first.is_primary = True

    if not claims:
        notes.append("No verifiable claims were extracted; using the title as the main claim.")
        return ("pr" if is_pr else out.report_kind), fallback_claims(inp, anchors), notes

    attach_anchors(claims, anchors, symbols_by_claim)
    return ("pr" if is_pr else out.report_kind), claims, notes
