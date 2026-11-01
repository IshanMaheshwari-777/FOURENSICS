"""Report signals and the suggested triage label (confirmed / invalid / cannot reproduce).

Signals are general wording patterns in the report itself. They contain no repository, project or
language names, so they behave the same on any issue. The label comes from code evidence first;
signals only decide a leaning when the evidence settles nothing. A leaning is a hint for the
maintainer, never a verdict.
"""
from __future__ import annotations

import re
from typing import Optional

from ..schemas import Anchor, Claim, ClaimAssessment, ReportKind, SuggestedLabel

# Hardware, drivers, devices and machine resources: problems that depend on the reporter's machine.
_ENV_TERMS = re.compile(
    r"\b(gpu|cuda|rocm|vulkan|opengl|directx|graphics card|driver|firmware|bios|hardware|chipset|"
    r"nvidia|radeon|geforce|amd|intel|esp32|esp8266|raspberry|microcontroller|"
    r"vram|ram|cpu|monitor|bluetooth|usb|printer|phone|tablet|"
    r"emulator|virtual machine|proxy|vpn|firewall|antivirus|locale|timezone)\b", re.I)
_INTERMITTENT = re.compile(
    r"\b(sometimes|intermittent(ly)?|randomly|random(ly)? (crash|fail|freez|hang)\w*|occasionally|sporadic(ally)?|"
    r"not always|from time to time|once in a while|every now and then|flaky|rarely|after (a while|some time|"
    r"several|a few) \w+|inconsistent(ly)?|can'?t (always )?reproduce|cannot (always )?reproduce|"
    r"unable to reproduce|only (happens|occurs) (once|some))\b", re.I)
_THIRD_PARTY = re.compile(
    r"\b(upstream|third[- ]party|3rd[- ]party|another (app|application|plugin|extension|library|tool|mod)|"
    r"other (plugins?|extensions?|mods?|apps?)|external (service|api|provider|server)|their (api|server|service|site|website)|"
    r"(the )?(site|website|service|provider|server) (changed|updated|is down|blocks?)|conflict(s|ing)? with)\b", re.I)
_UNSUPPORTED = re.compile(
    r"\b(not supported|unsupported|unofficial|custom (build|fork|rom|firmware)|modified (build|version|client)|"
    r"forked|patched (build|version)|outdated version|end[- ]of[- ]life|deprecated version)\b", re.I)
# A real reproduction: a code block, or a numbered or bulleted list of at least two steps.
# A template heading like "Steps to reproduce" with nothing under it does not count.
_LIST_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S", re.M)

SIGNAL_TEXT = {
    "environment_specific": "depends on the reporter's hardware, device or setup",
    "intermittent": "described as happening only sometimes",
    "no_repro_steps": "no steps or snippet to reproduce",
    "crash_log_only": "a crash log or error without steps to trigger it",
    "few_details": "several basic details are missing",
    "third_party": "points at another product, service or plugin",
    "unsupported_setup": "uses an unsupported or modified setup",
    "too_little_content": "too little content to check",
    "repro_steps": "includes steps or a snippet to reproduce",
    "expected_vs_actual": "states what was expected and what happened",
}

_OBSTACLES = ("environment_specific", "intermittent", "crash_log_only")
_INVALID = ("third_party", "unsupported_setup", "too_little_content")


def report_signals(text: str, anchors: list[Anchor], missing: list[str]) -> list[str]:
    """Signal codes for a defect report. `missing` is missing_info.check_missing's output."""
    kinds = {a.kind for a in anchors}
    words = re.findall(r"\w+", text)
    out: list[str] = []
    if len({m.group(0).lower() for m in _ENV_TERMS.finditer(text)}) >= 2:
        out.append("environment_specific")
    if _INTERMITTENT.search(text):
        out.append("intermittent")
    has_steps = "code_block" in kinds or len(_LIST_STEP.findall(text)) >= 2
    if not has_steps:
        out.append("no_repro_steps")
        if kinds & {"error", "traceback_frame"}:
            out.append("crash_log_only")
    else:
        out.append("repro_steps")
    if len(missing) >= 3:
        out.append("few_details")
    if "expected_actual" not in missing:
        out.append("expected_vs_actual")
    if _THIRD_PARTY.search(text):
        out.append("third_party")
    if _UNSUPPORTED.search(text):
        out.append("unsupported_setup")
    if len(words) < 25 and not kinds & {"error", "traceback_frame", "code_block", "symbol", "path"}:
        out.append("too_little_content")
    return out


def shown_label(s: Optional[SuggestedLabel]) -> Optional[SuggestedLabel]:
    """What results and the panel carry: evidence-backed labels only.

    On held-out issues, wording-based leanings scored no better than always guessing the most common
    label, so showing them would add noise. Evaluation's forced choice still uses them (eval_label)."""
    return s if s is not None and s.basis == "evidence" else None


def _primary(claims: list[Claim], assessments: list[ClaimAssessment]) -> Optional[ClaimAssessment]:
    primary = next((c for c in claims if c.is_primary), None)
    return next((a for a in assessments if primary and a.claim_id == primary.id), None)


def _reasons(codes: list[str]) -> list[str]:
    return [SIGNAL_TEXT[c] for c in codes if c in SIGNAL_TEXT]


def suggest_label(report_kind: Optional[ReportKind], claims: list[Claim], assessments: list[ClaimAssessment],
                  signals: list[str]) -> Optional[SuggestedLabel]:
    """Code evidence decides first ("evidence"); otherwise report signals give a leaning ("leaning")."""
    if report_kind == "pr":
        return None
    if report_kind in ("question", "feature", "docs"):
        kind = {"question": "a usage question", "feature": "a feature request", "docs": "a documentation request"}
        return SuggestedLabel(label="invalid", basis="evidence", reasons=[f"reads as {kind[report_kind]}, not a defect"])
    a = _primary(claims, assessments)
    sig = set(signals)
    if a is not None:
        if a.verification == "reproduced":
            return SuggestedLabel(label="confirmed", basis="evidence", reasons=["the reported behaviour was reproduced"])
        if a.technical == "contradicted":
            return SuggestedLabel(label="invalid", basis="evidence", reasons=["the code disagrees with the main claim"])
        if a.verification == "not_reproduced":
            return SuggestedLabel(label="cannot_reproduce", basis="evidence",
                                  reasons=["the reporter's snippet did not reproduce the problem"])
        if a.technical == "supported":
            # The failing code path exists. Whether maintainers can trigger it is a separate question
            # when the report gives no way to do so or says it happens only sometimes.
            if sig & {"crash_log_only", "intermittent"}:
                return SuggestedLabel(label="confirmed", basis="leaning",
                                      reasons=["the code matches the report"] + _reasons(sorted(sig & {"crash_log_only", "intermittent"})))
            return SuggestedLabel(label="confirmed", basis="evidence", reasons=["the code backs the main claim"])
        if a.lean == "contradicted":
            return SuggestedLabel(label="invalid", basis="leaning", reasons=["the evidence leans against the main claim"])

    # Nothing settled by the code. Report completeness is not evidence of a bug, so only a code
    # leaning points to confirmed; obstacles to reproducing point to cannot reproduce.
    leans_supported = a is not None and a.lean == "supported"
    invalid = [c for c in _INVALID if c in sig]
    obstacles = [c for c in _OBSTACLES if c in sig]
    if invalid and not leans_supported:
        return SuggestedLabel(label="invalid", basis="leaning", reasons=_reasons(invalid))
    if obstacles and not leans_supported:
        return SuggestedLabel(label="cannot_reproduce", basis="leaning", reasons=_reasons(obstacles))
    if leans_supported:
        return SuggestedLabel(label="confirmed", basis="leaning", reasons=["the evidence leans towards the main claim"])
    if {"no_repro_steps", "few_details"} <= sig:
        return SuggestedLabel(label="cannot_reproduce", basis="leaning",
                              reasons=_reasons(["no_repro_steps", "few_details"]))
    return None
